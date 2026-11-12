import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from pydantic import ValidationError
from rdflib import RDF
import issues
import rdf_manager as db
from app import app


class IssueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        root=Path(self.tmp.name)
        self.storage=patch.multiple(db,DATA_DIR=root,TTL_PATH=root/'knowledge.ttl',WIKI_DIR=root/'wiki',ARTICLES_DIR=root/'articles')
        self.storage.start()
        db.ensure_dirs()
        db.upsert_entity_with_relations('施設','Target')
        db.upsert_entity_with_relations('研究','Research')

    def tearDown(self):
        self.storage.stop()
        self.tmp.cleanup()

    def test_roundtrip_links_and_edits_preserve_other_knowledge(self):
        result=issues.save_issue(issues.IssueInput(title='送迎の見通し',description='朝の送迎で困る',reporter_role='住民',
            target_ids=['施設'],research_ids=['研究']))
        item=issues.list_issues()['items'][0]
        self.assertEqual('相談受付',item['status'])
        self.assertEqual(['施設'],[t['id'] for t in item['targets']])
        self.assertEqual(['研究'],[r['id'] for r in item['research']])
        self.assertTrue(item['created_at'])
        g=db.load_graph();node=db.RITOU[result['issue_id']]
        g.add((node,db.RITOU['関連する'],db.RITOU['施設']));db.save_graph(g)
        item=issues.list_issues()['items'][0]
        issues.save_issue(issues.IssueInput(issue_id=item['id'],expected_revision=item['revision'],title=item['title'],
                                           status='調査・検討中',new_target='別の施設'))
        g=db.load_graph()
        self.assertIn((node,db.RITOU['関連する'],db.RITOU['施設']),g)
        updated=issues.list_issues()['items'][0]
        self.assertEqual('調査・検討中',updated['status'])
        self.assertEqual([],updated['research'])
        self.assertEqual('別の施設',updated['targets'][0]['title'])
        self.assertEqual(item['created_at'],updated['created_at'])
        self.assertIn((db.RITOU['施設'],RDF.type,db.RITOU.Target),g)

    def test_location_is_owned_by_place_and_shared_by_all_its_issues(self):
        place=issues.list_places()['items'][0]
        issues.save_place(issues.PlaceInput(target_id=place['id'],expected_revision=place['revision'],
            title='弓削保育所',latitude=34.252179824041285,longitude=133.1890623153411))
        for title in ['坂道','駐車場']:
            issues.save_issue(issues.IssueInput(title=title,target_ids=['施設']))
        for item in issues.list_issues()['items']:
            self.assertNotIn('latitude',item)
            self.assertEqual('弓削保育所',item['targets'][0]['title'])
            self.assertEqual(34.252179824041285,item['targets'][0]['latitude'])
            self.assertFalse(list(db.load_graph().objects(db.RITOU[item['id']],db.RITOU['緯度'])))
        place=issues.list_places()['items'][0]
        issues.save_place(issues.PlaceInput(target_id=place['id'],expected_revision=place['revision'],
            title='弓削保育所',latitude=34.25,longitude=133.18))
        self.assertTrue(all(i['targets'][0]['latitude']==34.25 for i in issues.list_issues()['items']))
        self.assertEqual(2,len(list(db.load_graph().objects(db.RITOU['施設'],db.RITOU['抱える']))))

    def test_issue_can_create_an_informal_place_with_location_atomically(self):
        saved=issues.save_issue(issues.IssueInput(title='すれ違えない',new_target='保育所前の坂道',
                            new_target_latitude=0,new_target_longitude=0))
        item=issues.list_issues()['items'][0]
        target=item['targets'][0]
        self.assertEqual('保育所前の坂道',target['title'])
        self.assertEqual(0,target['latitude'])
        self.assertEqual(0,target['longitude'])
        self.assertNotEqual(target['id'],saved['issue_id'])
        self.assertFalse(list(db.load_graph().objects(db.RITOU[saved['issue_id']],db.RITOU['緯度'])))
        with self.assertRaises(ValidationError):
            issues.IssueInput(title='名前がない',new_target_latitude=34,new_target_longitude=133)
        with self.assertRaises(ValidationError):
            issues.IssueInput(title='イシューに直接座標',latitude=34,longitude=133)

    def test_unknown_places_and_multiple_places_are_supported(self):
        issues.save_issue(issues.IssueInput(title='場所がまだ分からない'))
        self.assertEqual([],issues.list_issues()['items'][0]['targets'])
        for name,lat in [('施設',34),('別施設',35)]:
            db.upsert_entity_with_relations(name,'Target',attributes={'緯度':str(lat),'経度':'133'})
        saved=issues.save_issue(issues.IssueInput(title='複数施設の相談',target_ids=['施設','別施設']))
        item=next(i for i in issues.list_issues()['items'] if i['id']==saved['issue_id'])
        self.assertEqual({34,35},{t['latitude'] for t in item['targets']})

    def test_invalid_references_and_stale_edits_do_not_write(self):
        saved=issues.save_issue(issues.IssueInput(title='困りごと'))
        before=db.TTL_PATH.read_bytes()
        for fields in [dict(issue_id=saved['issue_id'],expected_revision='old'),dict(target_ids=['研究']),
                       dict(research_ids=['不明']),dict(issue_id='不明')]:
            with self.subTest(fields=fields),self.assertRaises(ValueError):
                issues.save_issue(issues.IssueInput(title='変更',new_target='追加されてはいけない',
                                                  new_target_latitude=34,new_target_longitude=133,**fields))
            self.assertEqual(before,db.TTL_PATH.read_bytes())
        with self.assertRaises(issues.ConflictError):
            issues.save_place(issues.PlaceInput(target_id='施設',expected_revision='old',title='上書き'))
        self.assertEqual(before,db.TTL_PATH.read_bytes())

    def test_legacy_issues_are_untriaged_and_research_links_remain_visible(self):
        db.upsert_entity_with_relations('従来の課題','Issue',summary='従来の概要')
        db.upsert_entity_with_relations('研究','Research',relations=[{'predicate':'解決を試みる','object':'従来の課題'}])
        item=issues.list_issues()['items'][0]
        self.assertEqual('未整理',item['status'])
        self.assertEqual('従来の概要',item['description'])
        self.assertEqual('研究',item['research'][0]['id'])

    def test_api_validation_conflict_and_persistence(self):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                for body in [dict(title=' '),dict(title='x',latitude=12),dict(title='x',new_target='坂道',new_target_latitude=91,new_target_longitude=0),dict(title='x',status='other')]:
                    self.assertEqual(422,(await client.post('/api/issues',json=body)).status_code)
                r=await client.post('/api/issues',json={'title':'API投稿','reporter_role':'行政'})
                self.assertEqual(200,r.status_code)
                saved=r.json()
                r=await client.post('/api/issues',json={'issue_id':saved['issue_id'],'title':'上書き','expected_revision':'old'})
                self.assertEqual(409,r.status_code)
                items=(await client.get('/api/issues')).json()['items']
                self.assertEqual('API投稿',items[0]['title'])
                self.assertEqual('行政',items[0]['reporter_role'])
                r=await client.post('/api/places',json={'title':'海沿いの空き地','latitude':34,'longitude':133})
                self.assertEqual(200,r.status_code)
                places=(await client.get('/api/places')).json()['items']
                self.assertTrue(any(p['title']=='海沿いの空き地' and p['latitude']==34 for p in places))
                self.assertEqual(422,(await client.post('/api/places',json={'title':'不完全','latitude':34})).status_code)
        asyncio.run(run())
