import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from rdflib import Literal, RDF

import activity_records
import rdf_manager as db
from app import app, ActivityInput, ActivityRecordInput
from portfolio import save_activity


class ActivityRecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.storage = patch.multiple(db, DATA_DIR=root, TTL_PATH=root / 'knowledge.ttl',
                                     WIKI_DIR=root / 'wiki', ARTICLES_DIR=root / 'articles')
        self.storage.start()
        db.ensure_dirs()
        db.upsert_entity_with_relations('担当者', 'User', attributes={'表示名': '担当者の名前'})
        self.activity = save_activity(ActivityInput(title='試験用の研究', goal='将来の目標'))

    def tearDown(self):
        self.storage.stop()
        self.tmp.cleanup()

    def save(self, **kwargs):
        return activity_records.save_record(ActivityRecordInput(**{
            'activity_id': self.activity, 'recorded_by': '担当者', 'content': '試験を進めた', **kwargs}))

    def test_progress_is_append_only_and_own_input_is_not_verified(self):
        first = self.save(confirmation='原資料確認済み', activity_period='2025年10月')
        second = self.save(content='次の試験を実施した')
        g = db.load_graph()
        root = db.RITOU[self.activity]
        for record in (first, second):
            evidence, source = db.RITOU[record['record_id']], db.RITOU[record['source_id']]
            self.assertIn((root, db.RITOU['根拠記述'], evidence), g)
            self.assertEqual('未確認', str(g.value(evidence, db.RITOU['確認状況'])))
            self.assertEqual('入力記録', str(g.value(source, db.RITOU['資料区分'])))
            self.assertIn((source, db.RITOU['作成者'], db.RITOU['担当者']), g)
            self.assertEqual(root, g.value(evidence, db.RITOU['記述対象']))
        self.assertNotEqual(first['record_id'], second['record_id'])
        self.assertFalse(list(g.objects(root, db.RITOU['実績を持つ'])))
        self.assertFalse(list(g.objects(root, db.RITOU['参照資料'])))
        self.assertTrue(list(g.objects(root, db.RITOU['目指す'])))

    def test_implementation_and_evaluation_are_distinct_with_exact_evidence(self):
        for kind, expected in [('implementation', '実施実績'), ('evaluation', '評価結果')]:
            saved = self.save(record_kind=kind, source_mode='new', source_title='報告書',
                              source_url='https://example.org/report', source_published='2024年12月',
                              source_locator='p.12 図3', confirmation='原資料確認済み', activity_period='2024年9月')
            g = db.load_graph()
            result, evidence, source = (db.RITOU[saved[key]] for key in ('result_id', 'record_id', 'source_id'))
            self.assertEqual(expected, str(g.value(result, db.RITOU['種別'])))
            self.assertEqual(result, g.value(evidence, db.RITOU['記述対象']))
            self.assertEqual(source, g.value(evidence, db.RITOU['出典']))
            self.assertEqual('p.12 図3', str(g.value(evidence, db.RITOU['該当箇所'])))
            self.assertEqual('2024年9月', str(g.value(evidence, db.RITOU['活動時期'])))
            self.assertEqual('2024年12月', str(g.value(source, db.RITOU['発行時点'])))
            self.assertIn((db.RITOU[self.activity], db.RITOU['実績を持つ'], result), g)
            self.assertIn((db.RITOU[self.activity], db.RITOU['参照資料'], source), g)
            self.assertTrue(g.value(evidence, db.RITOU['記録時点']))

    def test_source_only_and_reuse_dont_fabricate_claims_or_overwrite_metadata(self):
        saved = self.save(record_kind='source', content='', source_mode='new', source_title='先に登録する資料',
                          source_description='著者と発行元', recorded_by='')
        g = db.load_graph()
        self.assertFalse(list(g.subjects(RDF.type, db.RITOU.Evidence)))
        self.assertFalse(list(g.subjects(RDF.type, db.RITOU.Result)))
        source = db.RITOU[saved['source_id']]
        triples = set(g.triples((source, None, None)))
        self.save(source_mode='existing', source_id=saved['source_id'], source_locator='資料全体',
                  source_title='書き換えない')
        g = db.load_graph()
        self.assertEqual(triples, set(g.triples((source, None, None))))
        self.assertEqual(1, len(list(g.subjects(RDF.type, db.RITOU.Source))))

    def test_invalid_input_never_leaves_partial_records_or_sources(self):
        before = db.TTL_PATH.read_bytes()
        invalid = [dict(activity_id='不明'), dict(record_kind='other'), dict(content=' '),
                   dict(recorded_by=''), dict(recorded_by='不明'), dict(source_mode='other'),
                   dict(record_kind='source'), dict(source_mode='new', source_title=''),
                   dict(source_mode='new', source_title='資料', source_locator=''),
                   dict(source_mode='new', source_title='資料', source_locator='p.1', source_url='javascript:alert(1)'),
                   dict(source_mode='new', source_title='資料', source_locator='p.1', confirmation='確認したつもり'),
                   dict(source_mode='existing', source_id='不明', source_locator='資料全体')]
        for fields in invalid:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.save(**fields)
            self.assertEqual(before, db.TTL_PATH.read_bytes())

    def test_api_returns_saved_record_and_rejects_invalid_request(self):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                response = await client.post('/api/portfolio/activity-record', json={
                    'activity_id': self.activity, 'recorded_by': '担当者', 'content': 'APIから登録'})
                self.assertEqual(200, response.status_code, response.text)
                self.assertTrue(response.json()['record_id'])
                response = await client.post('/api/portfolio/activity-record', json={'activity_id': self.activity})
                self.assertEqual(400, response.status_code)
                self.assertEqual(200, (await client.get('/static/activity-records.js')).status_code)
        asyncio.run(run())
