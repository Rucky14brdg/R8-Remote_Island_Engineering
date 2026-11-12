import asyncio
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from PIL import Image
from rdflib import RDF, Literal
import articles
import rdf_manager as db
import llm_agent
from app import app


class ArticleEditingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.paths = patch.multiple(db, DATA_DIR=root, TTL_PATH=root / 'knowledge.ttl', ARTICLES_DIR=root / 'articles', WIKI_DIR=root / 'wiki')
        self.paths.start()
        self.env = patch.dict(os.environ, {'RITOU_ARTICLE_EDITING': '1'})
        self.env.start()
        db.ensure_dirs()
        db.upsert_entity_with_relations('著者', 'User')
        db.upsert_entity_with_relations('現場', 'Target')
        db.upsert_entity_with_relations('技術', 'Technology')
        db.upsert_entity_with_relations('別の課題', 'Issue')
        db.upsert_entity_with_relations('研究', 'Research', relations=[{'predicate':'対象とする','object':'現場'},{'predicate':'用いる','object':'技術'}])

    def tearDown(self):
        self.env.stop()
        self.paths.stop()
        self.tmp.cleanup()

    def create(self, **kwargs):
        return db.save_article(**{'article_id':'','title':'記事の題名','article_type':'ニュース・発表','content':'元の本文','author':'著者', **kwargs})

    def row(self, aid):
        return next(a for a in db.get_all_data()['articles'] if a['id']==aid)

    def edit(self, aid, **kwargs):
        return db.save_article(**{'article_id':aid,'title':'編集後','article_type':'ニュース・発表','content':'編集後の本文', **kwargs})

    def png(self):
        stream=io.BytesIO()
        Image.new('RGB',(80,50),'#447766').save(stream,format='PNG')
        return stream.getvalue()

    def test_edit_preserves_publication_date_and_unedited_metadata_with_backup(self):
        aid=self.create()
        g=db.load_graph()
        g.set((db.RITOU[aid],db.RITOU['日付'],Literal('2020-01-02')))
        g.add((db.RITOU[aid],db.RITOU['説明'],Literal('保持するメタデータ')))
        db.save_graph(g)
        row=self.row(aid)
        self.edit(aid,expected_revision=row['revision'])
        updated=self.row(aid)
        self.assertEqual('2020-01-02',updated['date'])
        self.assertEqual(['著者'],updated['authors'])
        self.assertTrue(updated['modified_at'])
        self.assertNotEqual(row['revision'],updated['revision'])
        g=db.load_graph()
        self.assertEqual('保持するメタデータ',str(g.value(db.RITOU[aid],db.RITOU['説明'])))
        histories=list((db.DATA_DIR/'article_history'/aid).glob('*'))
        self.assertEqual(1,len(histories))
        self.assertEqual('元の本文',(histories[0]/'article.md').read_text())
        self.assertTrue((histories[0]/'article.ttl').is_file())

    def test_legacy_relations_are_read_without_mutating_then_saved_through_research(self):
        aid=self.create()
        g=db.load_graph()
        for name in ['研究','現場','技術','別の課題']:
            g.add((db.RITOU[aid],db.RITOU['関連する'],db.RITOU[name]))
        db.save_graph(g)
        before=db.TTL_PATH.read_bytes()
        row=self.row(aid)
        self.assertEqual(before,db.TTL_PATH.read_bytes())
        self.assertEqual(['研究'],row['related_research'])
        self.assertEqual(['別の課題'],row['additional_related'])
        self.assertEqual({'現場','技術'},set(row['covered_related']))
        self.edit(aid,related_research=row['related_research'],related_entities=row['additional_related'])
        g=db.load_graph()
        self.assertIn((db.RITOU[aid],db.RITOU['研究について'],db.RITOU['研究']),g)
        self.assertEqual([db.RITOU['別の課題']],list(g.objects(db.RITOU[aid],db.RITOU['関連する'])))
        self.assertIn((db.RITOU['研究'],db.RITOU['用いる'],db.RITOU['技術']),g)
        result=llm_agent.search_subgraph_and_docs(['技術'],db.get_all_data())
        self.assertIn(aid,[a['id'] for a in result['articles']])

    def test_explicit_supplement_is_kept_even_if_project_context_overlaps(self):
        aid=self.create(related_research=['研究'],related_entities=['技術'])
        self.assertEqual(['技術'],self.row(aid)['additional_related'])

    def test_invalid_research_is_rejected_before_any_write(self):
        aid=self.create()
        before=db.TTL_PATH.read_bytes()
        with self.assertRaises(ValueError):
            self.edit(aid,related_research=['技術'])
        self.assertEqual(before,db.TTL_PATH.read_bytes())
        self.assertEqual('元の本文',self.row(aid)['content'])
        self.assertFalse((db.DATA_DIR/'article_history').exists())

    def test_stale_revision_cannot_overwrite_newer_edit(self):
        aid=self.create()
        version=self.row(aid)['revision']
        self.edit(aid,expected_revision=version)
        before=db.TTL_PATH.read_bytes()
        with self.assertRaises(articles.ArticleConflict):
            self.edit(aid,expected_revision=version,content='競合する本文')
        self.assertEqual(before,db.TTL_PATH.read_bytes())
        self.assertEqual('編集後の本文',self.row(aid)['content'])

    def test_image_upload_is_decoded_and_reencoded_to_webp(self):
        url=articles.store_thumbnail(self.png())
        self.assertRegex(url,r'^/media/article-thumbnails/[a-f0-9]{64}\.webp$')
        with Image.open(articles._local_thumbnail(url)) as image:
            self.assertEqual('WEBP',image.format)
        aid=self.create(thumbnail_url=url,thumbnail_alt='設置した装置')
        self.edit(aid)
        self.assertEqual(url,self.row(aid)['thumbnail_url'])
        self.edit(aid,thumbnail_url='',thumbnail_alt='')
        self.assertEqual('',self.row(aid)['thumbnail_url'])
        self.assertTrue(articles._local_thumbnail(url).is_file())

    def test_reject_non_images_oversize_and_unsafe_thumbnail_urls(self):
        for data in [b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',b'not an image',b'x'*(articles.MAX_UPLOAD_BYTES+1)]:
            with self.assertRaises(ValueError):
                articles.store_thumbnail(data)
        for url in ['javascript:alert(1)','/etc/passwd','https://user:password@example.com/image.png']:
            with self.assertRaises(ValueError):
                self.create(thumbnail_url=url)

    def test_editing_feature_can_be_disabled_without_blocking_readers(self):
        aid=self.create()
        with patch.dict(os.environ,{'RITOU_ARTICLE_EDITING':'0'}):
            self.assertFalse(db.get_all_data()['article_editing_enabled'])
            self.assertEqual('元の本文',self.row(aid)['content'])
            with self.assertRaises(PermissionError):
                self.edit(aid)

    def test_article_and_media_endpoints(self):
        async def run():
            aid=self.create()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                response=await client.post('/api/article',json={'title':'トップからの投稿','content':'お知らせ','entry_point':'news','article_type':'活動記録'})
                self.assertEqual(200,response.status_code)
                self.assertEqual('ニュース・発表',self.row(response.json()['article_id'])['article_type'])
                response=await client.post('/api/article/thumbnail',files={'file':('image.png',self.png(),'image/png')})
                self.assertEqual(200,response.status_code)
                media=await client.get(response.json()['url'])
                self.assertEqual(200,media.status_code)
                self.assertEqual('image/webp',media.headers['content-type'])
                response=await client.post('/api/article',json={'article_id':aid,'title':'編集後','content':'変更','related_research':['研究'],'related_entities':[]})
                self.assertEqual(200,response.status_code)
                response=await client.post('/api/article',json={'article_id':aid,'title':'編集後','content':'変更','expected_revision':'outdated'})
                self.assertEqual(409,response.status_code)
                with patch.dict(os.environ,{'RITOU_ARTICLE_EDITING':'0'}):
                    response=await client.post('/api/article',json={'article_id':aid,'title':'変更','content':'変更'})
                    self.assertEqual(403,response.status_code)
        asyncio.run(run())

    def test_top_entry_creates_news_even_if_a_different_kind_is_submitted(self):
        aid=self.create(entry_point='news',article_type='活動記録')
        self.assertEqual('ニュース・発表',self.row(aid)['article_type'])

    def test_research_news_keeps_its_research_and_rejects_invalid_context(self):
        aid=self.create(entry_point='news',context_id='研究',related_research=[])
        self.assertEqual('ニュース・発表',self.row(aid)['article_type'])
        self.assertEqual(['研究'],self.row(aid)['related_research'])
        before=db.TTL_PATH.read_bytes()
        with self.assertRaises(ValueError):
            self.create(entry_point='news',context_id='技術')
        self.assertEqual(before,db.TTL_PATH.read_bytes())

    def test_person_and_organization_activity_records_keep_their_portfolio(self):
        db.upsert_entity_with_relations('組織', 'Laboratory')
        for subject in ['著者','組織']:
            aid=self.create(entry_point='portfolio',context_id=subject,article_type='ニュース・発表')
            self.assertEqual('活動記録',self.row(aid)['article_type'])
            self.assertEqual([subject],self.row(aid)['record_subjects'])
        before=db.TTL_PATH.read_bytes()
        with self.assertRaises(ValueError):
            self.create(entry_point='portfolio',context_id='研究')
        self.assertEqual(before,db.TTL_PATH.read_bytes())

    def test_research_notes_require_a_research_and_cannot_be_unlinked(self):
        aid=self.create(entry_point='research',context_id='研究',article_type='ニュース・発表')
        self.assertEqual('研究・実践記事',self.row(aid)['article_type'])
        self.assertEqual(['研究'],self.row(aid)['related_research'])
        with self.assertRaises(ValueError):
            self.edit(aid,related_research=[],related_entities=[])
        with self.assertRaises(ValueError):
            self.create(entry_point='research',context_id='技術')

    def test_editing_does_not_reclassify_an_existing_article(self):
        aid=self.create()
        self.edit(aid,article_type='活動記録',entry_point='portfolio',context_id='著者')
        self.assertEqual('ニュース・発表',self.row(aid)['article_type'])
        self.assertEqual([],self.row(aid)['record_subjects'])
