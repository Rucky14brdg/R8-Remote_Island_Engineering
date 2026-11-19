import tempfile
import asyncio
import httpx
import unittest
from pathlib import Path
from unittest.mock import patch
from rdflib import RDF, Literal
import rdf_manager as db
import portfolio
from app import app, ActivityInput, ProfileInput, OrganizationInput


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

    def test_organization_creation_editing_and_membership_preservation(self):
        oid = portfolio.save_organization(OrganizationInput(name='組織 A & B', summary='概要',
            description='紹介文', affiliations=['大学'], external_links=['https://example.org/']))
        db.upsert_entity_with_relations('個人', 'User', relations=[{'predicate': '所属する', 'object': oid}])
        db.upsert_entity_with_relations(oid, 'Laboratory', relations=[{'predicate': '関連する', 'object': '大学'}])
        saved = portfolio.save_organization(OrganizationInput(entity_id=oid, name='変更後の組織',
            summary='更新した概要', description='更新した紹介', affiliations=['連携団体']))
        self.assertEqual(oid, saved)
        g = db.load_graph()
        self.assertEqual('変更後の組織', str(g.value(db.RITOU[oid], db.RITOU['タイトル'])))
        self.assertEqual('更新した紹介', db.get_wiki_content(oid))
        self.assertEqual({db.RITOU['連携団体']}, set(g.objects(db.RITOU[oid], db.RITOU['所属する'])))
        self.assertIn((db.RITOU['個人'], db.RITOU['所属する'], db.RITOU[oid]), g)
        self.assertIn((db.RITOU[oid], db.RITOU['関連する'], db.RITOU['大学']), g)
        self.assertFalse(list(g.objects(db.RITOU[oid], db.RITOU['外部リンク'])))

    def test_organization_invalid_edits_are_atomic(self):
        oid = portfolio.save_organization(OrganizationInput(name='組織', description='保持する紹介'))
        before = db.TTL_PATH.read_bytes()
        for body in [OrganizationInput(name='  '), OrganizationInput(name='組織'),
                     OrganizationInput(entity_id=oid, name='変更', affiliations=[oid]),
                     OrganizationInput(entity_id=oid, name='変更', external_links=['javascript:alert(1)'])]:
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    portfolio.save_organization(body)
                self.assertEqual(before, db.TTL_PATH.read_bytes())
                self.assertEqual('保持する紹介', db.get_wiki_content(oid))

    @patch.dict('os.environ', {'RITOU_ADMIN_TOKEN': 'test-key-' * 4})
    def test_organization_api(self):
        admin = {'Authorization': 'Bearer ' + 'test-key-' * 4}
        db.upsert_entity_with_relations('個人', 'User')
        db.upsert_entity_with_relations('下位組織', 'Laboratory')
        db.upsert_entity_with_relations('大学', 'Laboratory')

        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://test') as client:
                payload = {'name': '新規組織', 'affiliations': ['大学'],
                           'member_ids': ['個人'], 'child_organization_ids': ['下位組織']}
                denied = await client.post('/api/portfolio/organization', json=payload)
                self.assertEqual(403, denied.status_code)
                self.assertEqual(db.ADMIN_ENTITY_ID,
                                 (await client.get('/api/permissions', headers=admin)).json()['admin_entity_id'])
                response = await client.post('/api/portfolio/organization', json=payload, headers=admin)
                self.assertEqual(200, response.status_code, response.text)
                oid = response.json()['entity_id']
                membership = db.RITOU['所属する']
                self.assertEqual({db.RITOU['個人'], db.RITOU['下位組織']},
                                 set(db.load_graph().subjects(membership, db.RITOU[oid])))
                update = {'entity_id': oid, 'name': '名称変更', 'member_ids': [],
                          'child_organization_ids': ['未登録組織']}
                response = await client.post('/api/portfolio/organization', json=update, headers=admin)
                self.assertEqual(400, response.status_code)
                update['child_organization_ids'] = []
                response = await client.post('/api/portfolio/organization', json=update, headers=admin)
                self.assertEqual(200, response.status_code, response.text)
                graph = db.load_graph()
                self.assertFalse(list(graph.subjects(membership, db.RITOU[oid])))
                self.assertIn((db.RITOU[oid], membership, db.RITOU['大学']), graph)
                self.assertEqual('名称変更', str(graph.value(db.RITOU[oid], db.RITOU['タイトル'])))
        asyncio.run(check())

    @patch.dict('os.environ', {'RITOU_ADMIN_TOKEN': 'test-key-' * 4})
    def test_alternate_writes_require_admin_but_contributions_remain_open(self):
        db.upsert_entity_with_relations('既存_組織', 'Laboratory', wiki_content='保持する')
        db.upsert_entity_with_relations('個人', 'User',
            relations=[{'predicate': '所属する', 'object': '既存_組織'}])

        async def check():
            transport = httpx.ASGITransport(app=app, client=('192.168.1.10', 12345))
            async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
                before = db.TTL_PATH.read_bytes()
                requests = [
                    ('POST', '/api/portfolio/profile', {'entity_id': '個人', 'affiliations': []}),
                    ('POST', '/api/portfolio/profile', {'entity_id': db.ADMIN_ENTITY_ID, 'display_name': '改変'}),
                    ('POST', '/api/entity', {'entity_id': '新組織', 'entity_class': 'Laboratory'}),
                    ('POST', '/api/entity', {'entity_id': '個人', 'entity_class': 'User',
                                           'replace_predicates': ['https://example.org/ritou/所属する']}),
                    ('POST', '/api/wiki', {'entity_id': '既存 組織', 'wiki_content': '改変'}),
                    ('DELETE', '/api/entity/個人', None),
                    ('DELETE', '/api/article/既存_組織', None),
                    ('POST', '/api/turtle', {'content': db.get_raw_turtle()}),
                    ('POST', '/api/portfolio/activity', {'title': '研究', 'participants': [
                        {'name': '新組織', 'actor_class': 'Laboratory'}]}),
                ]
                for method, url, payload in requests:
                    with self.subTest(url=url, payload=payload):
                        response = await client.request(method, url, json=payload)
                        self.assertEqual(403, response.status_code, response.text)
                response = await client.post('/api/portfolio/organization', json={'name': '新組織'},
                    headers={'Authorization': 'Bearer ' + 'test-key-' * 4})
                self.assertEqual(403, response.status_code)
                self.assertEqual(before, db.TTL_PATH.read_bytes())
                self.assertEqual('保持する', db.get_wiki_content('既存_組織'))
                contributions = [
                    ('/api/portfolio/profile', {'entity_id': '個人', 'summary': '紹介'}),
                    ('/api/entity', {'entity_id': '新概念', 'entity_class': 'Resource'}),
                    ('/api/portfolio/activity', {'title': '研究', 'participants': [
                        {'name': '既存_組織', 'actor_class': 'Laboratory'}]}),
                    ('/api/article', {'title': '記事', 'content': '本文', 'author': '個人'}),
                ]
                for url, payload in contributions:
                    response = await client.post(url, json=payload)
                    self.assertEqual(200, response.status_code, response.text)
                self.assertIn((db.RITOU['個人'], db.RITOU['所属する'], db.RITOU['既存_組織']), db.load_graph())
        asyncio.run(check())

    def test_profile_organization_project_selection_is_optional_and_scoped(self):
        db.upsert_entity_with_relations('学校', 'Laboratory')
        db.upsert_entity_with_relations('別組織', 'Laboratory')
        db.upsert_entity_with_relations('個人', 'User',
            relations=[{'predicate': '所属する', 'object': '学校'}])

        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                payload = {'entity_id': '個人', 'organization_project_ids': ['学校']}
                response = await client.post('/api/portfolio/profile', json=payload)
                self.assertEqual(200, response.status_code, response.text)
                response = await client.post('/api/portfolio/profile', json={'entity_id': '個人'})
                self.assertEqual(200, response.status_code, response.text)
                preference = db.RITOU['活動を表示する所属組織']
                person = db.RITOU['個人']
                self.assertIn((person, preference, db.RITOU['学校']), db.load_graph())
                payload['organization_project_ids'] = ['別組織']
                response = await client.post('/api/portfolio/profile', json=payload)
                self.assertEqual(400, response.status_code, response.text)
                self.assertIn((person, preference, db.RITOU['学校']), db.load_graph())
                payload['organization_project_ids'] = []
                response = await client.post('/api/portfolio/profile', json=payload)
                self.assertEqual(200, response.status_code, response.text)
                graph = db.load_graph()
                self.assertFalse(list(graph.objects(person, preference)))
                self.assertIn((person, db.RITOU['所属する'], db.RITOU['学校']), graph)
        asyncio.run(check())

    def test_admin_public_profile_can_be_saved_without_changing_identity_or_membership(self):
        admin_id = db.ADMIN_ENTITY_ID
        db.upsert_entity_with_relations(admin_id, 'User', attributes={'表示名': admin_id},
            relations=[{'predicate': '所属する', 'object': '学校'}])

        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                payload = {'entity_id': admin_id, 'display_name': admin_id, 'summary': '公開プロフィール',
                           'description': '自己紹介', 'organization_project_ids': ['学校']}
                response = await client.post('/api/portfolio/profile', json=payload)
                self.assertEqual(200, response.status_code, response.text)
                response = await client.post('/api/portfolio/profile', json={**payload, 'display_name': '別の名前'})
                self.assertEqual(403, response.status_code)
                response = await client.post('/api/portfolio/profile', json={**payload, 'affiliations': ['学校', '別の組織']})
                self.assertEqual(403, response.status_code)
                response = await client.delete('/api/entity/' + admin_id)
                self.assertEqual(403, response.status_code)
                graph = db.load_graph()
                node = db.RITOU[admin_id]
                self.assertEqual('自己紹介', db.get_wiki_content(admin_id))
                self.assertEqual({db.RITOU['学校']}, set(graph.objects(node, db.RITOU['所属する'])))
                self.assertIn((node, db.RITOU['活動を表示する所属組織'], db.RITOU['学校']), graph)
        asyncio.run(check())

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
