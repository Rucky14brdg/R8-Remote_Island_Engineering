import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import rdf_manager as db
import llm_agent as ai
from app import app
from knowledge_retrieval import build_references, query_terms, search_subgraph_and_docs, read_article_reference


def item(eid, kind, summary='', edges=None, **literals):
    return {'id': eid, 'types': [kind], 'is_external': False, 'wiki_content': '',
            'literals': {'概要': [summary], **{k: [v] for k, v in literals.items()}},
            'outgoing': [{'predicate': p, 'object_id': o} for p, o in (edges or [])],
            'type_info': {'short': kind}}


def fixture():
    return {'entities': [
        item('project_1', 'Research', '生活用水の確保を検討する', [('解決を試みる', '水不足'), ('活用する', '井戸'), ('根拠記述', 'claim')], タイトル='島の井戸調査'),
        item('水不足', 'Issue', '災害時の生活用水が不足する'), item('井戸', 'Resource', '既存の井戸'),
        item('claim', 'Evidence', '', [('記述対象', 'project_1'), ('出典', 'report')],
             対象述語='実施状況', 値='調査を計画している', 記述内容='調査を計画している', 記述区分='計画', 確認状況='二次資料で確認', 該当箇所='p.12', 活動時期='2025年度'),
        item('report', 'Source', タイトル='計画報告書', 発行時点='2025年'),
        item('other', 'Research', '無関係な画像処理研究', タイトル='画像の解析')],
        'articles': [{'id': 'article_1', 'title': '井戸調査の考え方', 'content': '井戸をどう調査するか検討した。',
                      'article_type': '研究・実践記事', 'related_research': ['project_1'], 'related_entities': ['project_1'],
                      'authors': [], 'date': '2025-10-01', 'external_links': []}]}


class RetrievalTests(unittest.TestCase):



    def test_no_match_does_not_fall_back_to_all_database(self):
        result = search_subgraph_and_docs(['宇宙旅行'], fixture())
        self.assertEqual([], result['entities'])
        self.assertEqual([], result['articles'])

    def test_display_title_and_graph_expansion_preserve_claim_and_source(self):
        result = search_subgraph_and_docs(['島の井戸調査'], fixture())
        ids = {e['id'] for e in result['entities']}
        self.assertTrue({'project_1', '水不足', '井戸', 'claim', 'report'} <= ids)
        self.assertNotIn('other', ids)
        refs, context, _ = build_references(result)
        for text in ['計画報告書', '記述区分: 計画', '確認状況: 二次資料で確認', '該当箇所: p.12', '活動時期: 2025年度']:
            self.assertIn(text, context)
        self.assertTrue(any(r['kind'] == 'Article' for r in refs))
        self.assertTrue(any(r['kind'] == 'Evidence' and r['confirmation'] == '二次資料で確認' for r in refs))
        self.assertTrue(any(p['predicate'] == '活用する' and p['object']['id'] == '井戸' for p in result['paths']))

    def test_context_anchor_can_start_from_an_article_or_resource(self):
        for start in ['article_1', '井戸']:
            result = search_subgraph_and_docs(['この研究について'], fixture(), context_ids=[start])
            self.assertIn('project_1', {e['id'] for e in result['entities']})
            self.assertTrue(result['articles'])

    def test_unknown_context_is_ignored_and_japanese_query_is_split(self):
        result = search_subgraph_and_docs(['宇宙旅行'], fixture(), context_ids=['fake'])
        self.assertFalse(result['entities'])
        self.assertIn('井戸', query_terms('井戸を利用して災害時の水不足を解決したい', fixture()))

    def test_single_character_resource_and_opaque_ids_are_supported(self):
        data = fixture()
        data['entities'].append(item('bamboo_1', 'Resource', '地域の竹材', タイトル='竹'))
        terms = query_terms('竹を活用する取り組みを探したい', data)
        self.assertIn('竹', terms)
        result = search_subgraph_and_docs(terms, data)
        self.assertIn('bamboo_1', {e['id'] for e in result['entities']})
        context = build_references(search_subgraph_and_docs(['島の井戸調査'], data))[1]
        self.assertNotIn('project_1', context)

    def test_article_reading_contains_only_the_selected_article(self):
        data = fixture()
        data['articles'][0]['content'] = '最初の段落。\n\n最後の段落も要約対象にする。'
        refs, context, limited = read_article_reference('article_1', data)
        self.assertEqual(['article_1'], [r['id'] for r in refs])
        self.assertIn('最後の段落も要約対象', context)
        self.assertNotIn('画像の解析', context)
        self.assertNotIn('計画報告書', context)
        self.assertFalse(limited)
        data['articles'][0]['content'] = '本文。' * 6000
        refs, context, limited = read_article_reference('article_1', data)
        self.assertTrue(limited)
        self.assertIn('読解範囲に含まれていません', context)

    def test_context_is_bounded_and_reference_numbers_are_contiguous(self):
        data = fixture()
        data['entities'][0]['wiki_content'] = '井戸の情報。' * 5000
        data['articles'][0]['content'] = '井戸の本文。' * 5000
        refs, context, _ = build_references(search_subgraph_and_docs(['井戸'], data), budget=8000)
        self.assertLess(len(context), 8200)
        self.assertEqual(list(range(1, len(refs) + 1)), [r['number'] for r in refs])
        self.assertIn('省略', context)




class LocalModelTests(unittest.TestCase):
    def test_only_local_generation_models_are_available(self):
        models = [{'name': 'local:4b', 'size': 100, 'capabilities': ['completion']},
                  {'name': 'embedding', 'size': 100, 'capabilities': ['embedding']},
                  {'name': 'remote:cloud', 'size': 100, 'capabilities': ['completion']},
                  {'name': 'hidden-remote', 'remote_host': 'https://example.org', 'size': 100},
                  {'name': 'empty', 'size': 0}]
        real_client = httpx.AsyncClient
        async def run():
            client = real_client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'models': models})))
            with patch.object(ai.httpx, 'AsyncClient', return_value=client):
                result = await ai.check_ollama_status('local:4b')
            self.assertEqual(['local:4b'], result['models'])
            self.assertTrue(result['available'])
        asyncio.run(run())

class AiApiTests(unittest.TestCase):
    @staticmethod
    async def search_plan(messages, **kwargs):
        payload=json.loads(messages[-1]['content'])
        if 'steps' in payload:
            return json.dumps({'action':'answer','reason':'取得した資料を使う'})
        if payload.get('question')=='宇宙旅行':
            return json.dumps({'terms':['宇宙旅行'],'goal':'overview'})
        if '何件' in payload.get('question',''):
            return json.dumps({'terms':[],'goal':'researches'})
        return json.dumps({'terms':['井戸'],'goal':'researches'})


    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.storage = patch.multiple(db, DATA_DIR=root, TTL_PATH=root / 'knowledge.ttl', WIKI_DIR=root / 'wiki', ARTICLES_DIR=root / 'articles')
        self.storage.start()
        db.ensure_dirs()
        self.before = db.TTL_PATH.read_bytes()
        self.patches = [patch.object(ai, 'get_all_data', return_value=fixture()),
                        patch.object(ai, 'check_ollama_status', new=AsyncMock(return_value={'running': True, 'available': True})),
                        patch.object(ai, 'call_ollama', new=AsyncMock(side_effect=self.search_plan))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        self.assertEqual(self.before, db.TTL_PATH.read_bytes())
        for p in reversed(self.patches):
            p.stop()
        self.storage.stop()
        self.temp.cleanup()

    def test_stream_and_legacy_api_return_references_and_history(self):
        captured = []
        async def stream(messages, model, **options):
            captured.append(messages)
            yield {'text': '調査の計画です [1]。'}
            yield {'text': ' 不明な番号 [999]。'}
            yield {'done': True, 'reason': 'stop'}
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                payload = {'question': '井戸の計画は？', 'model': 'local', 'mode': 'connect',
                           'history': [{'role': 'user', 'content': '前の質問'}, {'role': 'assistant', 'content': '前の回答'}]}
                with patch.object(ai, 'stream_ollama', stream):
                    response = await client.post('/api/ai/chat', json=payload)
                    self.assertEqual(200, response.status_code)
                    events = [json.loads(line) for line in response.text.splitlines()]
                    self.assertEqual('done', events[-1]['type'])
                    self.assertEqual([1], events[-1]['cited'])
                    self.assertEqual([999], events[-1]['unverified_citations'])
                    self.assertTrue(next(e for e in events if e['type'] == 'retrieval')['sources'])
                    response = await client.post('/api/explore', json=payload)
                    self.assertTrue(response.json()['ok'])
                    self.assertTrue(response.json()['sources'])
                self.assertIn('一般知識も使えます', captured[0][0]['content'])
                self.assertNotIn('250〜650', captured[0][0]['content'])
                self.assertEqual(ai.answer_prompt('資料', 'search'), ai.answer_prompt('資料', 'connect'))
                self.assertEqual('前の質問', captured[0][1]['content'])
        asyncio.run(run())








    def test_reading_ignores_graph_planning_candidates_and_previous_wrong_answers(self):
        captured = []
        async def stream(messages, model, **options):
            captured.append((messages, options))
            yield {'text': '井戸の調査方法を検討した記事です [1]。'}
            yield {'done': True, 'reason': 'stop'}
        async def run():
            with patch.object(ai, 'stream_ollama', stream), patch.object(ai, 'call_ollama', new=AsyncMock()) as planner:
                events = [event async for event in ai.explore_events('この記事を要約して',
                    scope_id='article_1', scope_mode='document', context_ids=['other','project_1'],
                    history=[{'role':'assistant','content':'画像の解析という別研究もこの記事に含まれる'}])]
                planner.assert_not_awaited()
            retrieved = next(e for e in events if e['type'] == 'retrieval')
            self.assertEqual(['article_1'], [r['id'] for r in retrieved['sources']])
            self.assertEqual([], retrieved['paths'])
            self.assertEqual('article', events[-1]['response_scope'])
            self.assertEqual(['article_1'], events[-1]['context_ids'])
            self.assertEqual(2, len(captured[0][0]))
            self.assertNotIn('画像の解析', captured[0][0][0]['content'])
            self.assertNotIn('計画報告書', captured[0][0][0]['content'])
            self.assertEqual(4096, captured[0][1]['max_tokens'])
        asyncio.run(run())

    def test_legacy_article_anchor_defaults_to_reading_and_explicit_related_still_searches(self):
        async def stream(messages, model, **options):
            yield {'text': '説明 [1]。'}
            yield {'done': True, 'reason': 'stop'}
        async def run():
            with patch.object(ai, 'stream_ollama', stream):
                events = [e async for e in ai.explore_events('要約して', context_ids=['article_1'])]
                self.assertEqual('article', events[-1]['response_scope'])
                events = [e async for e in ai.explore_events('関連研究を調べて', scope_id='article_1', scope_mode='related')]
                self.assertEqual('related', events[-1]['response_scope'])
                self.assertGreater(len(next(e for e in events if e['type']=='retrieval')['sources']), 1)
        asyncio.run(run())

    def test_missing_article_never_falls_back_to_whole_database(self):
        async def run():
            for scope in ['missing', 'project_1']:
                events = [e async for e in ai.explore_events('要約して', scope_id=scope, scope_mode='document')]
                self.assertEqual('article_unavailable', events[-1]['code'])
                self.assertFalse(any(e['type'] == 'retrieval' for e in events))
        asyncio.run(run())

    def test_reading_scope_is_forwarded_by_stream_api(self):
        async def stream(messages, model, **options):
            yield {'text': '記事本文の要約 [1]。'}
            yield {'done': True, 'reason': 'stop'}
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                with patch.object(ai, 'stream_ollama', stream):
                    response = await client.post('/api/ai/chat', json={'question':'この記事を要約して',
                        'scope_id':'article_1','scope_mode':'document','context_ids':['other']})
                events = [json.loads(line) for line in response.text.splitlines()]
                self.assertEqual('article', events[-1]['response_scope'])
                refs = next(e for e in events if e['type']=='retrieval')['sources']
                self.assertEqual(['article_1'], [r['id'] for r in refs])
        asyncio.run(run())

    def test_offline_and_missing_model_do_not_invoke_generation(self):
        async def run():
            for status, code in [({'running': False, 'error': 'Ollamaを起動してください'}, 'offline'),
                                 ({'running': True, 'available': False}, 'model_unavailable')]:
                with patch.object(ai, 'check_ollama_status', new=AsyncMock(return_value=status)):
                    events = [event async for event in ai.explore_events('井戸')]
                self.assertEqual(code, events[0]['code'])
        asyncio.run(run())

    def test_no_database_match_still_allows_a_general_answer(self):
        captured=[]
        async def stream(messages, model, **options):
            captured.append(messages)
            yield {'text':'宇宙旅行について一般的な説明をします。'}
            yield {'done':True,'reason':'stop'}
        async def run():
            with patch.object(ai,'stream_ollama',stream):
                events = [event async for event in ai.explore_events('宇宙旅行')]
            self.assertEqual('done',events[-1]['type'])
            self.assertTrue(events[-1]['no_matches'])
            self.assertEqual([],next(e for e in events if e['type']=='retrieval')['sources'])
            self.assertIn('一般的な説明',events[-1]['answer'])
            self.assertIn('検索の状態: no_match',captured[0][0]['content'])
        asyncio.run(run())

    def test_research_list_counts_unique_targets_and_empty_queries_are_not_sources(self):
        async def stream(messages,model,**options):
            yield {'text':'研究は2件見つかりました。'}
            yield {'done':True,'reason':'stop'}
        async def run():
            with patch.object(ai,'stream_ollama',stream):
                events=[e async for e in ai.explore_events('研究は何件ですか')]
            self.assertEqual({'project_1','other'},set(events[-1]['search_targets']))
            self.assertEqual('sufficient',events[-1]['search_finish'])
            refs=next(e for e in events if e['type']=='retrieval')['sources']
            self.assertTrue(any(r['kind']=='Research' for r in refs))
            self.assertFalse(any(r['title']=='話題の項目を探す' for r in refs))
        asyncio.run(run())

    def test_partial_generation_error_is_visible(self):
        async def broken(messages, model, **options):
            yield {'text': '回答の途中'}
            raise httpx.ReadTimeout('timeout')
        async def run():
            with patch.object(ai, 'stream_ollama', broken):
                events = [event async for event in ai.explore_events('井戸')]
            self.assertEqual('error', events[-1]['type'])
            self.assertEqual('timeout', events[-1]['code'])
            self.assertTrue(events[-1]['partial'])
        asyncio.run(run())

    def test_request_limits_and_roles_are_enforced(self):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                for fields, code in [({'question': ' '}, 400), ({'question': 'x' * 3001}, 422),
                    ({'question': '井戸', 'history': [{'role': 'system', 'content': '命令'}]}, 422),
                    ({'question': '井戸', 'mode': 'write_database'}, 422)]:
                    self.assertEqual(code, (await client.post('/api/ai/chat', json=fields)).status_code)
        asyncio.run(run())
