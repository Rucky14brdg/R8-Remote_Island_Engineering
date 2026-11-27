import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from rdflib import RDF

import accounts
import rdf_manager as db
from app import app


class AccountTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.enterContext(patch.multiple(db, DATA_DIR=root, TTL_PATH=root / 'knowledge.ttl',
                                         WIKI_DIR=root / 'wiki', ARTICLES_DIR=root / 'articles'))
        self.headers = {'X-Ritou-Account': '1'}
        token = 'a' * 32
        self.enterContext(patch.dict('os.environ', RITOU_ADMIN_TOKEN=token))
        self.admin_headers = self.headers | {'Authorization': 'Bearer ' + token}
        self.client = await self.enterAsyncContext(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url='https://testserver', headers=self.headers))
        self.guest = await self.enterAsyncContext(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url='https://testserver', headers=self.headers))

    async def register(self, client, login_id):
        data = {'login_id': login_id, 'password': '1234ABCDabcd'}
        response = await client.post('/api/account/register', json=data)
        self.assertEqual(200, response.status_code, response.text)
        self.assertIn('HttpOnly', response.headers['Set-Cookie'])
        self.assertIn('Secure', response.headers['Set-Cookie'])
        duplicate = await self.guest.post('/api/account/register', json=data | {'login_id': login_id.upper()})
        self.assertEqual(409, duplicate.status_code)

    async def test_lan_http_allows_account_registration_and_login(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=('192.168.10.20', 51000)),
            base_url='http://192.168.10.10:8000',
        ) as client:
            credentials = {'login_id': 'i22003', 'password': '1234ABCDabcd'}
            blocked = await client.post('/api/account/register', json=credentials)
            self.assertEqual(403, blocked.status_code)
            client.headers.update(self.headers)
            created = await client.post('/api/account/register', json=credentials)
            self.assertEqual(200, created.status_code, created.text)
            self.assertNotIn('Secure', created.headers['Set-Cookie'])
            self.assertEqual(200, (await client.post('/api/account/logout')).status_code)
            self.assertEqual(200, (await client.post('/api/account/login', json=credentials)).status_code)
            account = (await client.get('/api/account')).json()['account']
            self.assertEqual('i22003', account['login_id'])

    async def test_new_password_requires_twelve_characters_and_three_categories(self):
        for password in ('1234ABCDabc', '12345678ABCD', '123456789012'):
            for endpoint, body, headers in (
                ('register', {'login_id': 'i22003', 'password': password}, self.headers),
                ('setup-ishibashi', {'password': password}, self.admin_headers),
            ):
                with self.subTest(endpoint=endpoint, password=password):
                    response = await self.client.post('/api/account/' + endpoint, json=body, headers=headers)
                    self.assertEqual(400, response.status_code, response.text)
                    self.assertIn('うち3種類以上', response.json()['detail'])
        await self.register(self.client, 'i22003')

    async def test_only_admin_can_set_up_reserved_login(self):
        self.assertTrue((await self.guest.get('/api/account')).json()['ishibashi_setup_required'])
        password = {'password': '1234ABCDabcd'}
        credentials = password | {'login_id': 'I22002'}
        self.assertEqual(409, (await self.client.post('/api/account/register', json=credentials)).status_code)
        self.assertEqual(403, (await self.client.post('/api/account/setup-ishibashi', json=password)).status_code)
        response = await self.guest.post('/api/account/setup-ishibashi', json=password, headers=self.admin_headers)
        self.assertEqual(200, response.status_code, response.text)
        self.assertFalse((await self.guest.get('/api/account')).json()['ishibashi_setup_required'])
        response = await self.guest.post('/api/account/setup-ishibashi', json=password, headers=self.admin_headers)
        self.assertEqual(409, response.status_code)
        self.assertEqual(200, (await self.client.post('/api/account/login', json=credentials)).status_code)
        account = (await self.client.get('/api/account')).json()['account']
        self.assertEqual('i22002', account['login_id'])
        self.assertEqual(db.ADMIN_ENTITY_ID, account['person_id'])
        edit = await self.client.post('/api/portfolio/profile', json={
            'entity_id': db.ADMIN_ENTITY_ID, 'summary': '自己紹介'
        })
        self.assertEqual(200, edit.status_code, edit.text)
        self.assertEqual(403, (await self.client.get('/api/account/claims')).status_code)

    async def test_legacy_email_session_is_revoked_and_profile_can_be_claimed_again(self):
        db.upsert_entity_with_relations('tanaka', 'User')
        with sqlite3.connect(db.DATA_DIR / 'accounts.sqlite3') as connection:
            connection.executescript("""
                CREATE TABLE accounts (id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL,
                    salt TEXT NOT NULL, password_hash TEXT NOT NULL, person_id TEXT UNIQUE);
                CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, account_id INTEGER NOT NULL, expires INTEGER NOT NULL);
                INSERT INTO accounts VALUES (1, 'old@example.org', 'salt', 'hash', 'tanaka');
            """)
            connection.execute('INSERT INTO sessions VALUES (?, 1, ?)',
                               (accounts.digest('legacy-cookie'), int(time.time()) + 600))
        self.client.cookies.set(accounts.COOKIE, 'legacy-cookie')
        self.assertIsNone((await self.client.get('/api/account')).json()['account'])
        self.client.cookies.clear()
        self.assertEqual(404, (await self.client.post('/api/account/code', json={})).status_code)

        await self.register(self.client, 'tanaka')
        claim = await self.client.post('/api/account/claim', json={'person_id': 'tanaka'})
        self.assertEqual(200, claim.status_code, claim.text)
        pending = (await self.guest.get('/api/account/claims', headers=self.admin_headers)).json()[0]
        self.assertEqual('tanaka', pending['login_id'])
        approved = await self.guest.post('/api/account/claims/' + pending['id'] + '/approve', headers=self.admin_headers)
        self.assertEqual(200, approved.status_code, approved.text)
        self.assertEqual('tanaka', (await self.client.get('/api/account')).json()['account']['person_id'])

    async def test_pending_claim_preserves_links_and_only_approved_owner_can_edit(self):
        for identifier in ('tanaka_a', 'tanaka_b'):
            db.upsert_entity_with_relations(identifier, 'User', attributes={'表示名': '田中 太郎', '肩書': '学生'})
        person = db.RITOU.tanaka_a
        graph = db.load_graph()
        graph.add((db.RITOU.article, RDF.type, db.RITOU.Article))
        graph.add((db.RITOU.article, db.RITOU['作成者'], person))
        db.save_graph(graph)

        await self.register(self.client, 'tanaka')
        candidates = (await self.client.get('/api/account/people', params={'q': '田中太郎'})).json()
        self.assertEqual(['tanaka_a', 'tanaka_b'], [person['id'] for person in candidates])
        self.assertEqual('学生', candidates[0]['headline'])
        self.assertNotIn('records', candidates[0])
        self.assertEqual([], (await self.client.get('/api/account/people', params={'q': 'tanaka_a'})).json())
        claim = {'person_id': 'tanaka_a'}
        result = await self.client.post('/api/account/claim', json=claim)
        self.assertEqual(200, result.status_code, result.text)
        self.assertEqual(403, (await self.client.get('/api/account/claims')).status_code)
        self.assertIsNone((await self.client.get('/api/account')).json()['account']['person_id'])

        pending = (await self.guest.get('/api/account/claims', headers=self.admin_headers)).json()
        review = '/api/account/claims/' + pending[0]['id'] + '/approve'
        self.assertEqual(403, (await self.client.post(review)).status_code)
        self.assertEqual(200, (await self.guest.post(review, headers=self.admin_headers)).status_code)
        self.assertEqual('tanaka_a', (await self.client.get('/api/account')).json()['account']['person_id'])
        self.assertIn((db.RITOU.article, db.RITOU['作成者'], person), db.load_graph())

        edit = {'entity_id': 'tanaka_a', 'summary': '自己紹介'}
        self.assertEqual(403, (await self.guest.post('/api/portfolio/profile', json=edit)).status_code)
        self.assertEqual(200, (await self.client.post('/api/portfolio/profile', json=edit)).status_code)
        self.assertEqual(200, (await self.client.post('/api/account/logout')).status_code)
        self.assertEqual(403, (await self.client.post('/api/portfolio/profile', json=edit | {'summary': ''})).status_code)
        self.assertEqual(200, (await self.client.post('/api/account/login', json={
            'login_id': 'tanaka', 'password': '1234ABCDabcd'
        })).status_code)
        self.assertEqual('tanaka_a', (await self.client.get('/api/account')).json()['account']['person_id'])

    async def test_new_profile_claim_creates_a_distinct_person_after_approval(self):
        db.upsert_entity_with_relations('known_tanaka', 'User', attributes={'表示名': '田中 太郎'})
        await self.register(self.client, 'other')
        requested = await self.client.post('/api/account/claim', json={
            'name': '田中 太郎'
        })
        self.assertEqual(200, requested.status_code, requested.text)
        before = set(db.load_graph().subjects(RDF.type, db.RITOU.User))
        self.assertEqual({db.RITOU.known_tanaka, db.RITOU[db.ADMIN_ENTITY_ID]}, before)
        account = (await self.client.get('/api/account')).json()['account']
        review = '/api/account/claims/' + account['claim']['id'] + '/approve'
        approved = await self.guest.post(review, headers=self.admin_headers)
        self.assertEqual(200, approved.status_code, approved.text)
        linked = (await self.client.get('/api/account')).json()['account']['person_id']
        self.assertNotEqual('known_tanaka', linked)
        graph = db.load_graph()
        self.assertEqual('田中 太郎', str(graph.value(db.RITOU[linked], db.RITOU['表示名'])))

    async def test_same_name_can_be_entered_twice_with_stable_ids(self):
        people = ['person_' + value * 32 for value in 'ab']
        activity = {'entity_id': 'research_test', 'title': '研究', 'participants': [
            {'name': '田中 太郎', 'new_person_id': person, 'role': '担当'} for person in people
        ]}
        for _ in range(2):
            result = await self.client.post('/api/portfolio/activity', json=activity)
            self.assertEqual(200, result.status_code, result.text)
        graph = db.load_graph()
        self.assertEqual({db.RITOU[person] for person in people},
                         set(graph.subjects(RDF.type, db.RITOU.User)) - {db.RITOU[db.ADMIN_ENTITY_ID]})
