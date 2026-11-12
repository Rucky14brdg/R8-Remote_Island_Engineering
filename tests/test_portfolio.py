import tempfile
import asyncio
import httpx
import unittest
from pathlib import Path
from unittest.mock import patch
from rdflib import RDF, Literal
import rdf_manager as db
import portfolio
from app import app, ActivityInput, ProfileInput


class PortfolioInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patch = patch.multiple(db, DATA_DIR=root, TTL_PATH=root / 'knowledge.ttl',
                                    WIKI_DIR=root / 'wiki', ARTICLES_DIR=root / 'articles')
        self.patch.start()
        db.ensure_dirs()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def save(self, **kwargs):
        return portfolio.save_activity(ActivityInput(**{'title': '試験活動', **kwargs}))

    def test_title_only_is_enough_and_id_is_stable_when_renamed(self):
        aid = self.save()
        self.save(entity_id=aid, title='変更後の名称')
        g = db.load_graph()
        self.assertEqual('変更後の名称', str(g.value(db.RITOU[aid], db.RITOU['タイトル'])))
        self.assertEqual(1, len(list(g.subjects(RDF.type, db.RITOU.Research))))

    def test_reference_creation_and_role_save_in_one_operation(self):
        aid = self.save(targets=['対象の島'], issues=['移動の困難'], technologies=['調査方法'],
                        resources=['既存の設備'], participants=[{'name': '試験者', 'role': '担当'}])
        g = db.load_graph()
        for name, cls in [('対象の島', 'Target'), ('移動の困難', 'Issue'), ('調査方法', 'Technology'), ('既存の設備', 'Resource')]:
            self.assertIn((db.RITOU[name], RDF.type, db.RITOU[cls]), g)
        record = g.value(db.RITOU[aid], db.RITOU['参加関係'])
        self.assertEqual(db.RITOU['試験者'], g.value(record, db.RITOU['主体']))
        self.assertEqual('担当', str(g.value(record, db.RITOU['役割'])))

    def test_invalid_member_does_not_leave_partial_references(self):
        before = db.TTL_PATH.read_bytes()
        with self.assertRaises(ValueError):
            self.save(targets=['保存しない島'], participants=[{'name': '試験者', 'role': ''}], description='保存しない説明')
        self.assertEqual(before, db.TTL_PATH.read_bytes())
        self.assertFalse(list(db.WIKI_DIR.glob('*.md')))

    def test_type_collision_is_explained_without_changing_existing_item(self):
        db.upsert_entity_with_relations('既存項目', 'Resource', summary='保持する')
        before = db.TTL_PATH.read_bytes()
        with self.assertRaisesRegex(ValueError, '別の種類'):
            self.save(technologies=['既存項目'])
        self.assertEqual(before, db.TTL_PATH.read_bytes())

    def test_legacy_activity_can_be_edited_without_losing_sources(self):
        aid = self.save(technologies=['技術A'])
        db.upsert_entity_with_relations('資料A', 'Source', attributes={'タイトル': '原資料'})
        db.upsert_entity_with_relations('既存の記述', 'Evidence',
            attributes={'対象述語': '概要', '値': '既存記述', '記述区分': '資料の説明',
                        '確認状況': '原資料確認済み', '該当箇所': 'p.1'},
            relations=[{'predicate': '記述対象', 'object': aid}, {'predicate': '出典', 'object': '資料A'}])
        g = db.load_graph()
        g.add((db.RITOU['担当者'], RDF.type, db.RITOU.User))
        g.add((db.RITOU['担当者'], db.RITOU['扱う'], db.RITOU[aid]))
        db.save_graph(g)
        self.save(entity_id=aid, title='変更', participants=None)
        g = db.load_graph()
        self.assertIn((db.RITOU[aid], db.RITOU['根拠記述'], db.RITOU['既存の記述']), g)
        self.assertIn((db.RITOU['担当者'], db.RITOU['扱う'], db.RITOU[aid]), g)

    def test_member_removal_is_scoped_to_one_activity(self):
        people=[{'name': '試験者', 'role': '講師'}]
        first = self.save(participants=people)
        second = self.save(participants=people)
        self.save(entity_id=first, participants=[])
        g = db.load_graph()
        self.assertFalse(list(g.objects(db.RITOU[first], db.RITOU['参加関係'])))
        self.assertTrue(list(g.objects(db.RITOU[second], db.RITOU['参加関係'])))

    def test_profile_retains_project_links_and_does_not_replace_person_id(self):
        aid = self.save(participants=[{'name': '試験者', 'role': '担当'}])
        portfolio.save_profile(ProfileInput(entity_id='試験者', display_name='表示名', affiliations=['組織A'], keywords=['研究分野']))
        g = db.load_graph()
        self.assertEqual('表示名', str(g.value(db.RITOU['試験者'], db.RITOU['表示名'])))
        self.assertTrue(list(g.subjects(db.RITOU['主体'], db.RITOU['試験者'])))

    def test_goal_is_not_an_evaluated_result(self):
        aid = self.save(goal='将来の目標')
        g = db.load_graph()
        node = g.value(db.RITOU[aid], db.RITOU['目指す'])
        self.assertIn((node, RDF.type, db.RITOU.Goal), g)
        self.assertNotIn((node, RDF.type, db.RITOU.Result), g)

    def test_freeform_markdown_retains_rdf_goals_and_related_items(self):
        markdown = '## 自由な見出し\n\n**試したこと**\n\n- 観察\n- 製作\n\n| 項目 | 内容 |\n| --- | --- |\n| 結果 | 調査中 |\n\n[現場](#entity/対象の島)\n\n![装置](https://example.com/device.png)'
        aid=self.save(goal='既存の目標',targets=['対象の島'],issues=['困りごと'],description='旧本文')
        self.save(entity_id=aid,goal='既存の目標',targets=['対象の島'],issues=['困りごと'],description=markdown)
        row=next(e for e in db.get_all_data()['entities'] if e['id']==aid)
        self.assertEqual(markdown,row['wiki_content'])
        g=db.load_graph()
        self.assertIn((db.RITOU[aid],db.RITOU['対象とする'],db.RITOU['対象の島']),g)
        self.assertIn((db.RITOU[aid],db.RITOU['解決を試みる'],db.RITOU['困りごと']),g)
        goals=list(g.objects(db.RITOU[aid],db.RITOU['目指す']))
        self.assertEqual(1,len(goals))
        self.assertEqual('既存の目標',str(g.value(goals[0],db.RITOU['概要'])))
        self.assertFalse(list(g.objects(db.RITOU[aid],db.RITOU['実績を持つ'])))

    def test_unsafe_link_is_rejected_before_write(self):
        before=db.TTL_PATH.read_bytes()
        with self.assertRaises(ValueError):
            self.save(external_links=['javascript:alert(1)'])
        self.assertEqual(before, db.TTL_PATH.read_bytes())

    def test_freeform_reference_names_roundtrip_with_spaces_and_symbols(self):
        aid = self.save(resources=['IoT センサー（試作 / 1）', 'C++', '設備.'], participants=[{'name': '山田 太郎', 'role': '調査'}])
        g = db.load_graph()
        resources = list(g.objects(db.RITOU[aid], db.RITOU['活用する']))
        self.assertEqual({'IoT センサー（試作 / 1）', 'C++', '設備.'}, {str(g.value(r, db.RITOU['タイトル'])) for r in resources})
        self.assertTrue(list(g.subjects(RDF.type, db.RITOU.Participation)))

    def test_portfolio_endpoints_and_assets(self):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                for path in ['/', '/static/portfolio.js', '/static/portfolio.css', '/advanced']:
                    self.assertEqual(200, (await client.get(path)).status_code)
                response = await client.post('/api/portfolio/activity', json={'title': ''})
                self.assertEqual(400, response.status_code)
                response = await client.post('/api/portfolio/activity', json={'title': '名称だけの活動'})
                self.assertEqual(200, response.status_code)
                self.assertTrue(response.json()['entity_id'])
        asyncio.run(run())
