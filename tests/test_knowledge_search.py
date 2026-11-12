import asyncio
import json
import unittest
from unittest.mock import AsyncMock

from knowledge_search import SearchTools, search_events, MAX_OPERATIONS
from tests.test_island_ai import item


def transport_fixture():
    data={'entities':[
        item('place_opaque','Target',表示名='弓削保育所',edges=[('抱える','issue_slope')]),
        item('issue_slope','Issue','狭い坂で送迎車がすれ違えない',タイトル='坂の離合問題'),
        item('traffic_project','Research','送迎時間帯の単車線道路を信号で制御する',タイトル='交互交通制御システム',
             edges=[('対象とする','place_opaque'),('解決を試みる','issue_slope'),('用いる','esp'),('参加関係','role_traffic')]),
        item('esp','Technology','車両を検知する制御機器',表示名='ESP32'),
        item('unrelated','Research','RDFを使った知識基盤を設計する',タイトル='知識基盤の構築',edges=[('参加関係','role_other')]),
        item('teacher','User',表示名='共通の教員'),
        item('role_traffic','Participation',edges=[('主体','teacher'),('活動','traffic_project')]),
        item('role_other','Participation',edges=[('主体','teacher'),('活動','unrelated')])],
        'articles':[{'id':'progress','title':'中間発表の報告','content':'試作機で車両の検知を確認した。実地評価は今後の計画。',
                     'article_type':'ニュース・発表','date':'2026-10-25','authors':[],
                     'related_research':['traffic_project'],'related_entities':['traffic_project']}]}
    data['entities'][2]['wiki_content']='## 研究概要\n道路の交互通行を制御して送迎時の待ち時間を減らす研究。'
    return data


class ToolCompilerTests(unittest.TestCase):
    def test_find_all_types_then_follow_correct_directions_and_read_articles(self):
        async def run():
            tools=SearchTools(transport_fixture())
            found=await tools.run('find_entities',{'terms':['保育所']})
            self.assertTrue(found['result']['ok'],found)
            self.assertIn('place_opaque',{x['id'] for x in found['result']['items']})
            related=await tools.run('get_related',{'ids':['place_opaque'],'relation':'researches'})
            self.assertTrue(related['result']['ok'],related)
            self.assertEqual({'traffic_project'},{x['id'] for x in related['result']['items']})
            for relation,expected in [('technologies','esp'),('articles','progress'),('members','teacher')]:
                step=await tools.run('get_related',{'ids':['traffic_project'],'relation':relation})
                self.assertTrue(step['result']['ok'],step)
                self.assertIn(expected,{x['id'] for x in step['result']['items']})
            read=await tools.run('read_documents',{'ids':['traffic_project','progress']})
            self.assertTrue(read['result']['ok'],read)
            text=' '.join(r['text']['value'] for r in read['result']['rows'])
            self.assertIn('道路の交互通行',text)
            self.assertIn('実地評価は今後の計画',text)
        asyncio.run(run())

    def test_research_roles_are_scoped_and_article_author_is_not_a_participant(self):
        from knowledge_retrieval import build_references
        from copy import deepcopy
        async def run():
            data=transport_fixture()
            data['entities'] += [
                item('writer','User',表示名='記事の執筆者'),
                item('student','User',表示名='研究の学生'),
                item('student_role','Participation',役割='主体',edges=[('主体','student'),('活動','traffic_project')]),
                item('writer_role','Participation',役割='主体',edges=[('主体','writer'),('活動','unrelated')])]
            data['entities'][2]['outgoing'].append({'predicate':'参加関係','object_id':'student_role'})
            data['entities'][4]['outgoing'].append({'predicate':'参加関係','object_id':'writer_role'})
            data['entities'][5]['outgoing'] += [{'predicate':'扱う','object_id':'traffic_project'},{'predicate':'担当する','object_id':'unrelated'}]
            data['entities'][6]['literals']['役割']=['担当教員']
            data['entities'][7]['literals']['役割']=['協力者']
            data['articles'][0]['authors']=['writer']
            original=deepcopy(data)
            tools=SearchTools(data)
            step=await tools.run('get_related',{'ids':['traffic_project','unrelated'],'relation':'members'})
            self.assertTrue(step['result']['ok'],step)
            scoped=tools.research_participations
            self.assertEqual({('student','主体'),('teacher','担当教員')},
                             {(m['actor_id'],m['role']) for m in scoped['traffic_project']['members']})
            self.assertEqual({('writer','主体'),('teacher','協力者')},
                             {(m['actor_id'],m['role']) for m in scoped['unrelated']['members']})
            _,context,_=build_references({**data,'keywords':[], 'research_participations':scoped})
            self.assertIn('研究の学生: 主体',context)
            self.assertIn('共通の教員: 担当教員',context)
            self.assertIn('この記事を書いた人（著者）: 記事の執筆者',context)
            self.assertIn('著者 記事の執筆者 と研究 交互交通制御システム の関係: 今回の研究別参加記録では参加を確認できません',context)
            data['articles'][0]['related_research']=['unrelated']
            _,context,_=build_references({**data,'keywords':[], 'research_participations':scoped})
            self.assertIn('著者 記事の執筆者 と研究 知識基盤の構築 の関係: 登録された役割: 主体',context)
            data['articles'][0]['related_research']=['traffic_project']
            self.assertEqual(original,data)
        asyncio.run(run())

    def test_failed_or_absent_membership_lookup_is_unknown(self):
        from knowledge_retrieval import build_references
        from unittest.mock import patch
        async def run():
            data=transport_fixture();tools=SearchTools(data)
            with patch('knowledge_search.execute_query',new=AsyncMock(return_value={'ok':False,'rows':[],'error':'timeout'})):
                await tools.run('get_related',{'ids':['traffic_project'],'relation':'members'})
            self.assertNotIn('traffic_project',tools.research_participations)
            _,context,_=build_references({**data,'keywords':[], 'research_participations':tools.research_participations})
            self.assertIn('今回未確認',context)
            result=await tools.run('get_related',{'ids':['esp'],'relation':'members'})
            self.assertTrue(result['result']['ok'])
        asyncio.run(run())

    def test_registered_alias_is_searchable_and_the_same_topic_reaches_research(self):
        async def run():
            data=transport_fixture();data['entities'][0]['literals']['別名']=['ゆげの園']
            tools=SearchTools(data)
            result=await tools.run('find_entities',{'terms':['ゆげの園']})
            self.assertTrue(result['result']['ok'],result)
            self.assertEqual('place_opaque',result['result']['items'][0]['id'])
            self.assertEqual(100,result['result']['items'][0]['score'])
        asyncio.run(run())

    def test_literals_are_escaped_and_unknown_ids_do_not_become_queries(self):
        async def run():
            tools=SearchTools(transport_fixture())
            result=await tools.run('find_entities',{'terms':['")) } UNION { ?s ?p ?o #']})
            self.assertTrue(result['result']['ok'],result)
            self.assertEqual([],result['result']['items'])
            invalid=await tools.run('read_documents',{'ids':['file:///etc/passwd']})
            self.assertEqual('invalid_arguments',invalid['result']['status'])
            self.assertEqual('',invalid['query'])
            raw=await tools.run('query',{'query':'SELECT * WHERE {?s ?p ?o}'})
            self.assertFalse(raw['result']['ok'])
        asyncio.run(run())

    def test_missing_text_and_long_text_are_distinct_from_missing_item(self):
        async def run():
            data=transport_fixture();data['entities'][0]['wiki_content']='文'*7000
            tools=SearchTools(data)
            partial=await tools.run('read_documents',{'ids':['place_opaque']})
            self.assertTrue(partial['result']['more_available'])
            self.assertEqual(5600,partial['result']['next_offset'])
            rest=await tools.run('read_documents',{'ids':['place_opaque'],'offset':5600})
            self.assertFalse(rest['result']['more_available'])
            empty=await tools.run('read_documents',{'ids':['teacher']})
            self.assertEqual('no_text',empty['result']['status'])
            self.assertIn('teacher',tools.read_ids)
        asyncio.run(run())


class SemanticRetrievalTests(unittest.TestCase):
    def test_everyday_questions_reach_research_without_title_keyword(self):
        async def run():
            for question in ['保育所に関する研究について知っている限りを教えてください',
                             '送り迎えに関する研究を教えて', '坂道の研究はありますか']:
                # 語句抽出に失敗しても、原文と別名から最低限の検索を実施する。
                generate=AsyncMock(side_effect=[json.dumps({'terms':[],'goal':'researches'}),json.dumps({'action':'answer','reason':'本文を確認した'})])
                events=[e async for e in search_events(question,transport_fixture(),'local',[],[],generate)]
                result=events[-1]
                self.assertEqual('sufficient',result['finish_reason'],question)
                self.assertEqual(['traffic_project'],result['targets'],question)
                self.assertNotIn('unrelated',result['read_ids'],question)
                self.assertIn('progress',result['read_ids'],question)
                self.assertTrue(all(s['result']['ok'] for s in result['steps']),question)
                self.assertEqual(['find_entities','get_related','get_related','get_related','read_documents'],[s['operation'] for s in result['steps']])
                prompt=json.loads(generate.call_args_list[1].args[0][1]['content'])
                self.assertIn('実地評価は今後の計画',json.dumps(prompt,ensure_ascii=False))
        asyncio.run(run())

    def test_agent_can_request_technology_after_the_baseline_read(self):
        plans=[{'terms':['保育所'],'goal':'researches'},
               {'action':'get_related','ids':['traffic_project'],'relation':'technologies'},
               {'action':'read_documents','ids':['esp']},
               {'action':'answer','reason':'技術の説明も確認した'}]
        generate=AsyncMock(side_effect=[json.dumps(p,ensure_ascii=False) for p in plans])
        async def run():
            events=[e async for e in search_events('保育所の研究と技術を教えて',transport_fixture(),'local',[],[],generate)]
            result=events[-1]
            self.assertEqual('sufficient',result['finish_reason'])
            self.assertIn('esp',result['read_ids'])
            self.assertEqual(7,len(result['steps']))
        asyncio.run(run())

    def test_no_match_is_not_a_query_error_or_an_empty_source(self):
        generate=AsyncMock(side_effect=[json.dumps({'terms':['火星'],'goal':'researches'}),json.dumps({'action':'answer','reason':'候補が見つからない'})])
        async def run():
            events=[e async for e in search_events('火星に関する研究は？',transport_fixture(),'local',[],[],generate)]
            result=events[-1]
            self.assertEqual('no_match',result['finish_reason'])
            self.assertEqual([],result['retrieved']['entities'])
            self.assertTrue(result['steps'][0]['result']['ok'])
        asyncio.run(run())

    def test_many_results_report_partial_reading_instead_of_full_completion(self):
        data={'entities':[item('p'+str(i),'Research',タイトル='研究'+str(i)) for i in range(10)],'articles':[]}
        for ent in data['entities']:ent['wiki_content']='研究の詳細な本文です。'
        generate=AsyncMock(side_effect=[json.dumps({'terms':[],'goal':'researches'}),json.dumps({'action':'answer','reason':'取得した範囲で回答'})])
        async def run():
            result=[e async for e in search_events('研究一覧を見せてください',data,'local',[],[],generate)][-1]
            self.assertEqual('partial',result['finish_reason'])
            self.assertTrue(result['retrieved']['limited'])
            unread=[e for e in result['retrieved']['entities'] if e['id'] not in result['read_ids']]
            self.assertEqual(2,len(unread))
            self.assertTrue(all(not e['wiki_content'] for e in unread))
        asyncio.run(run())
