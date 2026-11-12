import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from rdflib import RDF

import activity_records
import articles
import rdf_manager as db
from app import app, ActivityInput, ActivityRecordInput
from portfolio import save_activity


class ResearchOrganizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.storage = patch.multiple(db, DATA_DIR=root, TTL_PATH=root / 'knowledge.ttl',
                                     WIKI_DIR=root / 'wiki', ARTICLES_DIR=root / 'articles')
        self.storage.start()
        db.ensure_dirs()
        db.upsert_entity_with_relations('author', 'User')
        self.project = save_activity(ActivityInput(title='研究A', summary='概要は手動で保持'))
        self.other = save_activity(ActivityInput(title='研究B'))
        self.content = '## 試作と評価\n\n模型を製作した。🔧\n\n**評価**：10回中9回成功した。\n'
        self.article_id = articles.save_article('', '試作と検討', '研究・実践記事', self.content,
                                               author='author', entry_point='research', context_id=self.project)
        self.refresh_revision()

    def refresh_revision(self):
        g = db.load_graph()
        self.revision = articles.revision(g, db.RITOU[self.article_id], db.get_all_data()['articles'][0]['content'])

    def tearDown(self):
        self.storage.stop()
        self.tmp.cleanup()

    def body(self, **fields):
        return ActivityRecordInput(**{'activity_id': self.project, 'record_kind': 'implementation',
            'source_mode': 'article', 'article_id': self.article_id, 'article_revision': self.revision,
            'article_excerpt': '模型を製作した。🔧', 'recorded_by': 'author', **fields})

    def test_selected_text_becomes_evidence_without_changing_article_or_overview(self):
        before = (db.ARTICLES_DIR / (self.article_id + '.md')).read_bytes()
        saved = activity_records.save_record(self.body(content='クライアントから送った別の主張', confirmation='原資料確認済み'))
        g = db.load_graph()
        evidence, source = db.RITOU[saved['record_id']], db.RITOU[saved['source_id']]
        self.assertEqual('模型を製作した。🔧', str(g.value(evidence, db.RITOU['値'])))
        self.assertEqual('未確認', str(g.value(evidence, db.RITOU['確認状況'])))
        self.assertEqual(db.RITOU[self.article_id], g.value(source, db.RITOU['原記事']))
        self.assertEqual(self.revision, str(g.value(source, db.RITOU['記事版'])))
        self.assertEqual(self.content, str(g.value(source, db.RITOU['説明'])))
        self.assertEqual('概要は手動で保持', str(g.value(db.RITOU[self.project], db.RITOU['概要'])))
        self.assertEqual(before, (db.ARTICLES_DIR / (self.article_id + '.md')).read_bytes())
        self.assertFalse(list((db.DATA_DIR / 'article_history').glob('*')))

    def test_retries_are_idempotent_and_distinct_claims_share_one_snapshot(self):
        first = activity_records.save_record(self.body())
        self.assertEqual(first, activity_records.save_record(self.body()))
        second = activity_records.save_record(self.body(record_kind='evaluation', article_excerpt='**評価**：10回中9回成功した。'))
        g = db.load_graph()
        self.assertEqual(first['source_id'], second['source_id'])
        self.assertNotEqual(first['result_id'], second['result_id'])
        self.assertEqual(2, len(list(g.subjects(RDF.type, db.RITOU.Result))))
        self.assertEqual('評価結果', str(g.value(db.RITOU[second['result_id']], db.RITOU['種別'])))

    def test_later_article_edit_keeps_old_snapshot_and_stale_selection_is_rejected(self):
        saved = activity_records.save_record(self.body())
        articles.save_article(self.article_id, '更新後の記事', '研究・実践記事', '新しい本文', expected_revision=self.revision)
        before = db.TTL_PATH.read_bytes()
        with self.assertRaisesRegex(ValueError, '更新'):
            activity_records.save_record(self.body())
        self.assertEqual(before, db.TTL_PATH.read_bytes())
        g = db.load_graph()
        self.assertEqual(self.content, str(g.value(db.RITOU[saved['source_id']], db.RITOU['説明'])))

    def test_wrong_research_nonexistent_text_and_wrong_kind_never_write(self):
        before = db.TTL_PATH.read_bytes()
        for fields in [{'activity_id': self.other}, {'article_excerpt': '本文にない結論'},
                       {'article_excerpt': ' '}, {'record_kind': 'source'}, {'record_kind': 'progress'},
                       {'article_revision': ''}, {'article_id': 'missing'}]:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                activity_records.save_record(self.body(**fields))
            self.assertEqual(before, db.TTL_PATH.read_bytes())

    def test_api_organization_persists_versioned_source(self):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                response = await client.post('/api/portfolio/activity-record', json=self.body().model_dump())
                self.assertEqual(200, response.status_code, response.text)
                data = (await client.get('/api/data')).json()
                source = next(e for e in data['entities'] if e['id'] == response.json()['source_id'])
                self.assertEqual([self.revision], source['literals']['記事版'])
                self.assertEqual(200, (await client.get('/static/research-workspace.js')).status_code)
        asyncio.run(run())
