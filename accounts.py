"""Keep credentials, sessions, and person ownership outside the public RDF graph."""
from contextlib import contextmanager
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import string
import time
import unicodedata

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from rdflib import Literal, RDF

import rdf_manager as db

COOKIE = 'ritou_session'
SESSION_LIFETIME = 7 * 86400
ISHIBASHI_LOGIN_ID = 'i22002'
router = APIRouter(prefix='/api/account')


@contextmanager
def connect(*, write=False):
    db.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = db.DATA_DIR / 'accounts.sqlite3'
    os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY, login_id TEXT NOT NULL UNIQUE,
                salt TEXT NOT NULL, password_hash TEXT NOT NULL,
                person_id TEXT UNIQUE
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, account_id INTEGER NOT NULL, expires INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS attempts (
                key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS claims (
                id TEXT PRIMARY KEY, account_id INTEGER NOT NULL UNIQUE,
                person_id TEXT, name TEXT NOT NULL, evidence TEXT NOT NULL
            );
        """)
        migrate_email_accounts(connection)
        if write:
            connection.execute('BEGIN IMMEDIATE')
        with connection:
            yield connection
    finally:
        connection.close()


def migrate_email_accounts(connection):
    if 'email' not in {row['name'] for row in connection.execute('PRAGMA table_info(accounts)')}:
        return
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        if 'email' in {row['name'] for row in connection.execute('PRAGMA table_info(accounts)')}:
            connection.execute('ALTER TABLE accounts RENAME COLUMN email TO login_id')
            connection.execute("UPDATE accounts SET login_id = ':' || login_id")
            connection.execute('DELETE FROM sessions')
            connection.execute('DROP TABLE IF EXISTS verification')


def linked_account(connection, person_id):
    return connection.execute('SELECT id, login_id FROM accounts WHERE person_id = ?', (person_id,)).fetchone()


def active_account(account):
    return account is not None and not account['login_id'].startswith(':')


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=1,
                          maxmem=64 * 1024 * 1024).hex()


def current_account(connection, token):
    if not token or len(token) > 128:
        return None
    return connection.execute(
        'SELECT accounts.* FROM accounts JOIN sessions ON accounts.id = sessions.account_id '
        "WHERE token_hash = ? AND expires > ? AND accounts.login_id NOT LIKE ':%'",
        (digest(token), int(time.time()))
    ).fetchone()


def require_account(connection, request):
    account = current_account(connection, request.cookies.get(COOKIE))
    if account is None:
        raise HTTPException(401, 'ログインしてください')
    return account


def mutation(request):
    # A custom header forces a CORS preflight for cross-origin requests.
    if request.headers.get('X-Ritou-Account') != '1':
        raise HTTPException(403, 'この画面から操作してください')


def throttle(request, operation, login_id, *, limit, seconds):
    now = int(time.time())
    address = request.client.host if request.client else 'unknown'
    with connect(write=True) as connection:
        connection.execute('DELETE FROM attempts WHERE expires <= ?', (now,))
        keys = [digest(f'{operation}:id:{login_id}'), digest(f'{operation}:ip:{address}')]
        for key, maximum in zip(keys, (limit, limit * 10)):
            previous = connection.execute('SELECT count FROM attempts WHERE key = ?', (key,)).fetchone()
            if previous and previous['count'] >= maximum:
                raise HTTPException(429, '時間をおいてから再度お試しください')
            connection.execute(
                'INSERT INTO attempts VALUES (?, 1, ?) ON CONFLICT(key) DO UPDATE SET count = count + 1',
                (key, now + seconds)
            )


class LoginInput(BaseModel):
    login_id: str = Field(max_length=64)
    password: str = Field(max_length=128)

    def normalized_id(self):
        login_id = self.login_id.strip().lower()
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{2,31}', login_id):
            raise HTTPException(400, 'IDは半角英数字で始まる3〜32文字で入力してください（ハイフン・アンダースコア可）')
        return login_id


class InitialPassword(BaseModel):
    password: str = Field(max_length=128)


def validate_new_password(password):
    characters = set(password)
    groups = (string.ascii_uppercase, string.ascii_lowercase, string.digits, string.punctuation)
    category_count = sum(not characters.isdisjoint(group) for group in groups)
    if len(password) < 12 or category_count < 3:
        raise HTTPException(
            400, 'パスワードは12文字以上で、半角の大文字・小文字・数字・記号のうち3種類以上含めてください'
        )


class ClaimInput(BaseModel):
    person_id: str | None = Field(default=None, max_length=500)
    name: str = Field(default='', max_length=100)


def start_session(connection, request, response, account_id):
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    connection.execute('DELETE FROM sessions WHERE expires <= ? OR token_hash = ?',
                       (now, digest(request.cookies.get(COOKIE, ''))))
    connection.execute('INSERT INTO sessions VALUES (?, ?, ?)',
                       (digest(token), account_id, now + SESSION_LIFETIME))
    response.set_cookie(COOKIE, token, max_age=SESSION_LIFETIME, httponly=True,
                        secure=request.url.scheme == 'https', samesite='lax')


def create_account(connection, login_id, salt, encoded_password, *, person_id=None):
    if connection.execute('SELECT 1 FROM accounts WHERE login_id = ?', (login_id,)).fetchone():
        raise HTTPException(409, 'このIDは登録済みです。ログイン画面からお進みください')
    return connection.execute(
        'INSERT INTO accounts (login_id, salt, password_hash, person_id) VALUES (?, ?, ?, ?)',
        (login_id, salt, encoded_password, person_id)
    ).lastrowid


@router.post('/register')
def register(body: LoginInput, request: Request, response: Response):
    mutation(request)
    login_id = body.normalized_id()
    if login_id == ISHIBASHI_LOGIN_ID:
        raise HTTPException(409, 'i22002 は石橋治樹さん用のIDです。「管理者設定」で初回パスワードを設定してください')
    validate_new_password(body.password)
    throttle(request, 'register', login_id, limit=8, seconds=900)
    salt = secrets.token_hex(16)
    encoded_password = password_hash(body.password, salt)
    with connect(write=True) as connection:
        account_id = create_account(connection, login_id, salt, encoded_password)
        start_session(connection, request, response, account_id)
    return {'ok': True}


@router.post('/setup-ishibashi')
def setup_ishibashi(body: InitialPassword, request: Request):
    mutation(request)
    db.require_organization_admin()
    validate_new_password(body.password)
    if (db.RITOU[db.ADMIN_ENTITY_ID], RDF.type, db.RITOU.User) not in db.load_graph():
        raise HTTPException(409, '石橋治樹の人物レコードが見つかりません')
    salt = secrets.token_hex(16)
    encoded_password = password_hash(body.password, salt)
    with connect(write=True) as connection:
        previous = linked_account(connection, db.ADMIN_ENTITY_ID)
        if active_account(previous):
            raise HTTPException(409, '石橋治樹のアカウントは設定済みです')
        if previous:
            connection.execute('UPDATE accounts SET person_id = NULL WHERE id = ?', (previous['id'],))
        create_account(connection, ISHIBASHI_LOGIN_ID, salt, encoded_password, person_id=db.ADMIN_ENTITY_ID)
    return {'ok': True}


@router.post('/login')
def login(body: LoginInput, request: Request, response: Response):
    mutation(request)
    login_id = body.normalized_id()
    throttle(request, 'login', login_id, limit=10, seconds=900)
    with connect() as connection:
        account = connection.execute('SELECT * FROM accounts WHERE login_id = ?', (login_id,)).fetchone()
    candidate = password_hash(body.password, account['salt'] if account else '0' * 32)
    if not account or not hmac.compare_digest(candidate, account['password_hash']):
        raise HTTPException(401, 'IDまたはパスワードが違います')
    with connect(write=True) as connection:
        start_session(connection, request, response, account['id'])
    return {'ok': True}


@router.post('/logout')
def logout(request: Request, response: Response):
    mutation(request)
    with connect(write=True) as connection:
        connection.execute('DELETE FROM sessions WHERE token_hash = ?', (digest(request.cookies.get(COOKIE, '')),))
    response.delete_cookie(COOKIE, secure=request.url.scheme == 'https', httponly=True, samesite='lax')
    return {'ok': True}


@router.get('')
def status(request: Request, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    result = {'account': None, 'ishibashi_setup_required': True}
    if not (db.DATA_DIR / 'accounts.sqlite3').is_file():
        return result
    with connect() as connection:
        result['ishibashi_setup_required'] = connection.execute(
            'SELECT 1 FROM accounts WHERE login_id = ?', (ISHIBASHI_LOGIN_ID,)
        ).fetchone() is None
        account = current_account(connection, request.cookies.get(COOKIE))
        if account:
            claim = connection.execute(
                'SELECT id, account_id, person_id, name FROM claims WHERE account_id = ?', (account['id'],)
            ).fetchone()
            result['account'] = {'login_id': account['login_id'], 'person_id': account['person_id'],
                                 'claim': dict(claim) if claim else None}
    return result


def person_summary(graph, node):
    def title(subject):
        return str(graph.value(subject, db.RITOU['表示名']) or
                   graph.value(subject, db.RITOU['タイトル']) or db.node_to_id(subject))

    memberships = set(graph.objects(node, db.RITOU['所属する']))
    return {'id': db.node_to_id(node), 'name': title(node),
            'affiliations': sorted(map(title, memberships)),
            'headline': str(graph.value(node, db.RITOU['肩書']) or '')}


def normalized(value):
    return ''.join(unicodedata.normalize('NFKC', value).casefold().split())


@router.get('/people')
def people(request: Request, response: Response, q: str = ''):
    response.headers['Cache-Control'] = 'no-store'
    with connect() as connection:
        require_account(connection, request)
    query = normalized(q[:100])
    if not query:
        return []
    graph = db.load_graph()
    results = [person_summary(graph, node) for node in set(graph.subjects(RDF.type, db.RITOU.User))
               if node != db.RITOU[db.ADMIN_ENTITY_ID]]
    matches = [person for person in results if query in normalized(person['name'])]
    return sorted(matches, key=lambda person: (person['name'], person['id']))


@router.post('/claim')
def claim(body: ClaimInput, request: Request):
    mutation(request)
    name = body.name.strip()
    if not body.person_id and not name:
        raise HTTPException(400, '人物候補を選ぶか、新しく登録する氏名を入力してください')
    if body.person_id:
        graph = db.load_graph()
        if body.person_id == db.ADMIN_ENTITY_ID or (db.RITOU[body.person_id], RDF.type, db.RITOU.User) not in graph:
            raise HTTPException(404, '人物が見つかりません')
    with connect(write=True) as connection:
        account = require_account(connection, request)
        if account['person_id']:
            raise HTTPException(409, 'プロフィールは紐づけ済みです')
        if body.person_id and active_account(linked_account(connection, body.person_id)):
            raise HTTPException(409, 'この人物にはすでにアカウントが紐づいています')
        # Existing databases require a value in the former evidence column.
        connection.execute(
            "INSERT OR REPLACE INTO claims (id, account_id, person_id, name, evidence) VALUES (?, ?, ?, ?, '')",
            (secrets.token_hex(16), account['id'], body.person_id, name)
        )
    return {'ok': True}


@router.get('/claims')
def claims(response: Response):
    db.require_organization_admin()
    response.headers['Cache-Control'] = 'no-store'
    with connect() as connection:
        rows = connection.execute('SELECT claims.id, claims.person_id, claims.name, accounts.login_id FROM claims JOIN accounts '
                                  "ON accounts.id = claims.account_id WHERE accounts.login_id NOT LIKE ':%' "
                                  'ORDER BY claims.rowid').fetchall()
    graph = db.load_graph()
    return [dict(row) | {'person': person_summary(graph, db.RITOU[row['person_id']])
                         if row['person_id'] else None} for row in rows]


@router.post('/claims/{claim_id}/{decision}')
async def review(claim_id: str, decision: str, request: Request):
    # Approval writes the RDF graph on the same event loop as the existing graph editors.
    mutation(request)
    db.require_organization_admin()
    if decision not in {'approve', 'reject'}:
        raise HTTPException(404, '操作が見つかりません')
    with connect(write=True) as connection:
        row = connection.execute(
            'SELECT claims.* FROM claims JOIN accounts ON accounts.id = claims.account_id '
            "WHERE claims.id = ? AND accounts.login_id NOT LIKE ':%'", (claim_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, '申請が見つかりません')
        if decision == 'approve':
            person_id = row['person_id'] or 'person_' + row['id']
            graph = db.load_graph()
            node = db.RITOU[person_id]
            if person_id == db.ADMIN_ENTITY_ID or (row['person_id'] and (node, RDF.type, db.RITOU.User) not in graph):
                raise HTTPException(409, '対象の人物が見つかりません')
            previous = linked_account(connection, person_id)
            if active_account(previous):
                raise HTTPException(409, 'すでに別のアカウントに紐づいています')
            if previous:
                connection.execute('UPDATE accounts SET person_id = NULL WHERE id = ?', (previous['id'],))
            if not row['person_id'] and (node, RDF.type, db.RITOU.User) not in graph:
                graph.add((node, RDF.type, db.RITOU.User))
                graph.add((node, db.RITOU['表示名'], Literal(row['name'])))
                db.validate_entity(graph, node)
                db.save_graph(graph)
            connection.execute('UPDATE accounts SET person_id = ? WHERE id = ?', (person_id, row['account_id']))
        connection.execute('DELETE FROM claims WHERE id = ?', (claim_id,))
    return {'ok': True}


def profile_ownership(token):
    if not (db.DATA_DIR / 'accounts.sqlite3').is_file():
        return set(), None
    with connect() as connection:
        owner = current_account(connection, token)
        linked = {db.RITOU[row['person_id']] for row in connection.execute(
            'SELECT person_id FROM accounts WHERE person_id IS NOT NULL'
        )}
        owned = db.RITOU[owner['person_id']] if owner and owner['person_id'] else None
        return linked, owned
