import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from rdflib import RDF, Graph
import rdf_manager as db
from app import app
import llm_agent


class DatabaseDesignTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patcher = patch.multiple(db, DATA_DIR=root, TTL_PATH=root / 'knowledge.ttl',
                                     ARTICLES_DIR=root / 'articles', WIKI_DIR=root / 'wiki')
        self.patcher.start()
        db.ensure_dirs()

    def tearDown(self):
        self.patcher.stop()
        self.temp.cleanup()

    def put(self, name, cls, **kwargs):
        return db.upsert_entity_with_relations(name, cls, **kwargs)

    def relation(self, predicate, obj):
        return {'predicate': predicate, 'object': obj}

    def evidence(self, **changes):
        payload = dict(attributes={'対象述語': '実施状況', '値': '調査中', '該当箇所': 'p.2',
                                   '記述区分': '実施状況', '確認状況': '二次資料で確認', '活動時期': '2025年'},
                       relations=[self.relation('記述対象', '活動A'), self.relation('出典', '資料A')])
        payload.update(changes)
        return payload

    def prepare_evidence(self):
        self.put('活動A', 'Research')
        self.put('資料A', 'Source', attributes={'タイトル': '活動報告', '発行時点': '2025年'})

    def test_schema_visible_without_importing_instances(self):
        data = db.get_all_data()
        self.assertIn('Resource', {c['id'] for c in data['classes']})
        self.assertNotIn('Resource', {db.node_to_id(c) for c in db.load_graph().subjects(RDF.type, db.RDF_CLASS_URI)})

    def test_resource_and_method_are_distinct(self):
        self.put('活動A', 'Research')
        self.put('設備A', 'Resource')
        self.put('方法A', 'Technology')
        self.put('活動A', 'Research', relations=[self.relation('活用する', '設備A'), self.relation('用いる', '方法A')])
        before = db.TTL_PATH.read_bytes()
        with self.assertRaises(ValueError):
            self.put('活動A', 'Research', relations=[self.relation('用いる', '設備A')])
        self.assertEqual(before, db.TTL_PATH.read_bytes())

    def test_activity_role_is_not_person_status(self):
        self.put('活動A', 'Research')
        self.put('学生チーム', 'ActorGroup')
        self.put('参加A', 'Participation', attributes={'役割': '講師'},
                 relations=[self.relation('主体', '学生チーム'), self.relation('活動', '活動A')])
        g = db.load_graph()
        self.assertIn((db.RITOU['活動A'], db.RITOU['参加関係'], db.RITOU['参加A']), g)
        with self.assertRaises(ValueError):
            self.put('参加B', 'Participation', attributes={'役割': '運営'})

    def test_claim_preserves_evidence_without_asserting_plan(self):
        self.prepare_evidence()
        self.put('資源A', 'Resource')
        payload = self.evidence()
        payload['attributes'].update({'対象述語': '活用する', '記述区分': '計画'})
        del payload['attributes']['値']
        payload['relations'].append(self.relation('関係先', '資源A'))
        self.put('記述A', 'Evidence', **payload)
        g = db.load_graph()
        self.assertNotIn((db.RITOU['活動A'], db.RITOU['活用する'], db.RITOU['資源A']), g)
        self.assertIn((db.RITOU['活動A'], db.RITOU['根拠記述'], db.RITOU['記述A']), g)
        result = llm_agent.search_subgraph_and_docs(['活動A'], db.get_all_data())
        context = llm_agent.format_retrieval_context(result)
        for value in ['資料名: 活動報告', '該当箇所: p.2', '確認状況: 二次資料で確認', '記述区分: 計画']:
            self.assertIn(value, context)

    def test_time_series_keeps_both_records(self):
        self.prepare_evidence()
        self.put('記述2025', 'Evidence', **self.evidence())
        payload = self.evidence()
        payload['attributes'].update({'値': '実施済み', '活動時期': '2026年'})
        self.put('記述2026', 'Evidence', **payload)
        self.assertEqual(2, len(list(db.load_graph().objects(db.RITOU['活動A'], db.RITOU['根拠記述']))))

    def test_missing_source_or_ambiguous_value_rejected_atomically(self):
        self.prepare_evidence()
        before = db.TTL_PATH.read_bytes()
        payload = self.evidence()
        payload['relations'] = [self.relation('記述対象', '活動A')]
        with self.assertRaises(ValueError):
            self.put('不完全', 'Evidence', wiki_content='保存しない', **payload)
        self.assertEqual(before, db.TTL_PATH.read_bytes())
        self.assertFalse((db.WIKI_DIR / '不完全.md').exists())
        payload = self.evidence()
        payload['relations'].append(self.relation('関係先', '資料A'))
        with self.assertRaises(ValueError):
            self.put('曖昧', 'Evidence', **payload)

    def test_form_update_preserves_other_metadata(self):
        self.prepare_evidence()
        self.put('記述A', 'Evidence', **self.evidence())
        self.put('活動A', 'Research', summary='維持する概要', attributes={'実施時期': '2025年'})
        self.put('活動A', 'Research', relations=[self.relation('用いる', '新技術')], replace_relations=True)
        self.put('活動A', 'Research', replace_predicates=['用いる'])
        g = db.load_graph()
        self.assertEqual('維持する概要', str(g.value(db.RITOU['活動A'], db.RITOU['概要'])))
        self.assertEqual('2025年', str(g.value(db.RITOU['活動A'], db.RITOU['実施時期'])))
        self.assertTrue(list(g.objects(db.RITOU['活動A'], db.RITOU['根拠記述'])))
        self.assertFalse(list(g.objects(db.RITOU['活動A'], db.RITOU['用いる'])))

    def test_result_must_distinguish_activity_and_evaluation(self):
        with self.assertRaises(ValueError):
            self.put('結果', 'Result', attributes={'種別': '目標'})
        self.put('結果', 'Result', attributes={'種別': '実施実績'})

    def test_turtle_roundtrip_handles_quotes_and_newlines(self):
        self.put('資料A', 'Source', attributes={'タイトル': '三重引用符 """ と改行\n、バックスラッシュ \\ を含む資料'})
        g = db.load_graph()
        reloaded = Graph().parse(data=db.serialize_clean_turtle(g), format='turtle')
        self.assertEqual(set(g), set(reloaded))

    def test_schema_and_validation_api(self):
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                self.assertEqual(200, (await client.get('/api/schema')).status_code)
                response = await client.post('/api/entity', json={'entity_id': '資料', 'entity_class': 'Source'})
                self.assertEqual(400, response.status_code)
                self.assertIn('タイトル', response.json()['detail'])
                self.assertEqual(200, (await client.get('/api/data')).status_code)
        asyncio.run(check())

    def test_raw_turtle_cannot_bypass_required_evidence_fields(self):
        before = db.TTL_PATH.read_bytes()
        valid, message = db.save_raw_turtle('@prefix : <https://example.org/ritou/> . :不完全 a :Evidence .')
        self.assertFalse(valid)
        self.assertIn('スキーマ検証エラー', message)
        self.assertEqual(before, db.TTL_PATH.read_bytes())

    def test_used_source_cannot_be_deleted_leaving_evidence_invalid(self):
        self.prepare_evidence()
        self.put('記述A', 'Evidence', **self.evidence())
        before = db.TTL_PATH.read_bytes()
        with self.assertRaises(ValueError):
            db.delete_entity('資料A')
        self.assertEqual(before, db.TTL_PATH.read_bytes())


if __name__ == '__main__':
    unittest.main()
