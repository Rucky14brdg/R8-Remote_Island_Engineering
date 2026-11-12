"""記事の一時編集・画像・研究を中心とした関連付け。"""
import hashlib
import io
import json
import os
import re
import warnings
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

from PIL import Image, ImageOps, UnidentifiedImageError
from rdflib import Graph, Literal, RDF, URIRef
import rdf_manager as db

MAX_UPLOAD_BYTES = 6 * 1024 * 1024


class ArticleConflict(ValueError):
    pass


def editing_enabled():
    return os.environ.get('RITOU_ARTICLE_EDITING', '1').lower() not in {'0', 'false', 'off'}


def media_dir():
    return db.DATA_DIR / 'media' / 'article-thumbnails'


def _local_thumbnail(value):
    match = re.fullmatch(r'/media/article-thumbnails/([a-f0-9]{64}\.webp)', value)
    return media_dir() / match[1] if match else None


def validate_thumbnail(value):
    if not value:
        return
    path = _local_thumbnail(value)
    if path is not None and path.is_file():
        return
    parsed = urlsplit(value)
    if parsed.scheme in {'http', 'https'} and parsed.hostname and not parsed.username and not parsed.password:
        return
    raise ValueError('サムネイルにはアップロードした画像か、http(s)の画像URLを指定してください')


def store_thumbnail(data):
    if not editing_enabled():
        raise PermissionError('記事の編集機能は現在無効です')
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise ValueError('画像は6MB以下のJPEG・PNG・WebPで選択してください')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in {'JPEG', 'PNG', 'WEBP'}:
                    raise ValueError('画像はJPEG・PNG・WebPに対応しています')
                if source.width * source.height > 24_000_000:
                    raise ValueError('画像は2400万画素以下で選択してください')
                image = ImageOps.exif_transpose(source)
                image.thumbnail((1600, 1000))
                image = image.convert('RGBA' if 'A' in image.getbands() else 'RGB')
                output = io.BytesIO()
                image.save(output, format='WEBP', quality=85, method=4)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError('画像を読み込めませんでした。JPEG・PNG・WebPを選んでください') from exc
    content = output.getvalue()
    filename = hashlib.sha256(content).hexdigest() + '.webp'
    folder = media_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_bytes(content)
    return '/media/article-thumbnails/' + filename


def associations(g, article):
    """研究から辿れる関係は表示時に計算し、記事に複製しない。"""
    direct = set(g.objects(article, db.RITOU['関連する']))
    research = set(g.objects(article, db.RITOU['研究について']))
    research.update(n for n in direct if (n, RDF.type, db.RITOU.Research) in g)
    coverage = set()
    project_predicates = ['対象とする', '解決を試みる', '用いる', '扱う', '活用する', '担当する']
    for project in research:
        for predicate in project_predicates:
            coverage.update(g.objects(project, db.RITOU[predicate]))
        actors = set(g.subjects(db.RITOU['担当する'], project)) | set(g.subjects(db.RITOU['扱う'], project))
        for record in g.subjects(db.RITOU['活動'], project):
            actors.update(g.objects(record, db.RITOU['主体']))
        coverage.update(actors)
        for actor in actors:
            coverage.update(g.objects(actor, db.RITOU['所属する']))
    extras = direct - research
    # 旧データの関連項目に、研究から辿れる項目があれば整理候補にする。
    return {'research': sorted(research, key=str), 'additional': sorted(extras if list(g.objects(article, db.RITOU['研究について'])) else extras - coverage, key=str),
            'covered': [] if list(g.objects(article, db.RITOU['研究について'])) else sorted(extras & coverage, key=str), 'inherited': sorted(coverage, key=str)}


def revision(g, article, content):
    triples = '\n'.join(sorted(f'{p.n3()} {o.n3()}' for p, o in g.predicate_objects(article)))
    return hashlib.sha256((triples + '\n' + content).encode()).hexdigest()


def save_article(article_id, title, article_type, content, author=None, related_entities=None,
                 external_links=None, related_research=None, thumbnail_url=None, thumbnail_alt=None,
                 expected_revision=None, entry_point=None, context_id=None):
    db.ensure_dirs()
    g = db.load_graph()
    clean_id = db.safe_filename(article_id) if article_id.strip() else 'article_' + uuid4().hex[:16]
    if article_id.strip() and clean_id != article_id.strip():
        raise ValueError('記事IDが不正です')
    node = db.RITOU[clean_id]
    exists = (node, RDF.type, db.RITOU.Article) in g
    if article_id.strip() and not exists:
        raise ValueError('編集する記事が見つかりません。新規記事として作成してください')
    if exists and not editing_enabled():
        raise PermissionError('記事の編集機能は現在無効です')
    # 種類は作成する場所で決まる。編集による種類の変更は行わない。
    if exists:
        article_type = str(g.value(node, db.RITOU['種別']) or article_type)
    elif entry_point is not None:
        kinds = {'news': 'ニュース・発表', 'portfolio': '活動記録', 'research': '研究・実践記事'}
        if entry_point not in kinds:
            raise ValueError('記事の作成元が不正です')
        article_type = kinds[entry_point]
        if entry_point == 'portfolio':
            subject = db.RITOU[context_id or '']
            if not any((subject, RDF.type, db.RITOU[kind]) in g for kind in ['User', 'Laboratory', 'ActorGroup']):
                raise ValueError('活動記録は個人・組織・集団のポートフォリオから作成してください')
            g.add((node, db.RITOU['記録先'], subject))
        elif entry_point == 'research' or (entry_point == 'news' and context_id):
            project = db.RITOU[context_id or '']
            if (project, RDF.type, db.RITOU.Research) not in g:
                raise ValueError('研究・実践記事は研究・活動の画面から作成してください')
            related_research = list(set(related_research or []) | {context_id})
    if not title.strip():
        raise ValueError('記事のタイトルを入力してください')
    if not content.strip():
        raise ValueError('記事の本文を入力してください')
    old_filename = str(g.value(node, db.RITOU['ファイルパス']) or (clean_id + '.md'))
    if Path(old_filename).name != old_filename:
        raise ValueError('記事のファイルパスが不正です')
    path = db.ARTICLES_DIR / old_filename
    old_content = path.read_text(encoding='utf-8') if path.exists() else ''
    if expected_revision and expected_revision != revision(g, node, old_content):
        raise ArticleConflict('別の操作で記事が更新されています。入力は下書きに残っています。一度記事を開き直して確認してください')
    old_graph = db.get_raw_turtle()
    old_subject = Graph()
    for triple in g.triples((node, None, None)):
        old_subject.add(triple)
    now = datetime.now(ZoneInfo('Asia/Tokyo'))
    timestamp = now.isoformat(timespec='seconds')
    g.add((node, RDF.type, db.RITOU.Article))
    for key, value in [('タイトル', title.strip()), ('種別', article_type.strip() or '研究・実践記事'), ('ファイルパス', old_filename)]:
        g.set((node, db.RITOU[key], Literal(value)))
    if not list(g.objects(node, db.RITOU['日付'])):
        g.add((node, db.RITOU['日付'], Literal(now.date().isoformat())))
    if exists:
        g.set((node, db.RITOU['更新日時'], Literal(timestamp)))
    if author is not None:
        g.remove((node, db.RITOU['作成者'], None))
        if author.strip():
            author_node = db.RITOU[author.strip().lstrip(':')]
            if (author_node, RDF.type, db.RITOU.User) not in g:
                raise ValueError('著者は登録済みの研究者から選択してください')
            g.add((node, db.RITOU['作成者'], author_node))
    # 新GUIは専用の研究関係と任意の補足項目を送る。旧GUIの関連項目も
    # 保存時にはResearchを専用の関係へ分ける。
    if related_research is not None or related_entities is not None:
        research_ids = set(related_research or [])
        extras = set(related_entities or [])
        if related_research is None:
            research_ids.update(db.node_to_id(n) for n in g.objects(node, db.RITOU['研究について']))
        research_ids.update(x for x in extras if (db.RITOU[x], RDF.type, db.RITOU.Research) in g)
        extras -= research_ids
        for predicate in ['関連する', '研究について']:
            g.remove((node, db.RITOU[predicate], None))
        for rid in research_ids:
            project = db.RITOU[rid]
            if (project, RDF.type, db.RITOU.Research) not in g:
                raise ValueError('関連研究は登録済みの研究・活動から選択してください')
            g.add((node, db.RITOU['研究について'], project))
        for eid in extras:
            target = db.RITOU[eid]
            if target == node or not list(g.objects(target, RDF.type)):
                raise ValueError('補足の関連項目は登録済みの項目から選択してください')
            g.add((node, db.RITOU['関連する'], target))
    if external_links is not None:
        g.remove((node, db.RITOU['外部リンク'], None))
        for link in external_links:
            parsed = urlsplit(link.strip())
            if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
                raise ValueError('関連リンクはhttp(s)のURLで指定してください')
            g.add((node, db.RITOU['外部リンク'], URIRef(link.strip())))
    if thumbnail_url is not None:
        validate_thumbnail(thumbnail_url)
        g.remove((node, db.RITOU['サムネイル'], None))
        if thumbnail_url:
            g.add((node, db.RITOU['サムネイル'], Literal(thumbnail_url)))
    if thumbnail_alt is not None:
        g.remove((node, db.RITOU['画像の説明'], None))
        if thumbnail_alt.strip():
            g.add((node, db.RITOU['画像の説明'], Literal(thumbnail_alt.strip())))
    if article_type == '研究・実践記事' and not associations(g, node)['research']:
        raise ValueError('研究・実践記事には、知識を残す対象の研究・活動を紐付けてください')
    db.validate_entity(g, node)
    if exists:
        folder = db.DATA_DIR / 'article_history' / clean_id / (now.strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8])
        folder.mkdir(parents=True)
        (folder / 'article.md').write_text(old_content, encoding='utf-8')
        (folder / 'article.ttl').write_text(old_subject.serialize(format='turtle'), encoding='utf-8')
        (folder / 'knowledge.ttl').write_text(old_graph, encoding='utf-8')
        (folder / 'revision.json').write_text(json.dumps({'article_id': clean_id, 'saved_at': timestamp}, ensure_ascii=False), encoding='utf-8')
    path.write_text(content, encoding='utf-8')
    db.save_graph(g)
    return clean_id
