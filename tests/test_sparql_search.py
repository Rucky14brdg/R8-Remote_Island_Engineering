import asyncio
import json
import unittest
from sparql_search import snapshot_graph, execute_query, validate_query
from tests.test_island_ai import fixture

class SparqlExecutionTests(unittest.TestCase):
    def test_local_select_join_text_and_aggregate(self):
        async def run():
            nt=snapshot_graph(fixture()).serialize(format='nt')
            result=await execute_query(nt,'SELECT ?r ?issue WHERE {?r a :Research; :解決を試みる ?issue}')
            self.assertTrue(result['ok'],result)
            self.assertIn('project_1',result['ids'])
            self.assertEqual(1,len(result['rows']))
            inverse=await execute_query(nt,'SELECT ?research WHERE {:水不足 :解決を試みる ?research}')
            self.assertEqual([],inverse['rows'])
            self.assertIn('関係の向き',inverse['hints'][0])
            result=await execute_query(nt,'SELECT (COUNT(?r) AS ?count) WHERE {?r a :Research}')
            self.assertEqual('2',result['rows'][0]['count']['value'])
            result=await execute_query(nt,'SELECT ?article ?text WHERE {?article :本文 ?text FILTER(CONTAINS(?text,"井戸"))}')
            self.assertIn('article_1',result['ids'])
            self.assertIn('どう調査',result['rows'][0]['text']['value'])
        asyncio.run(run())

    def test_no_update_or_remote_access_but_keywords_inside_literals_are_allowed(self):
        for query in ['DELETE WHERE {?s ?p ?o}', 'INSERT DATA {:a :b :c}',
                      'SELECT * WHERE {SERVICE <http://example.com> {?s ?p ?o}}',
                      'SELECT * FROM <file:///tmp/private> WHERE {?s ?p ?o}']:
            with self.assertRaises(Exception):validate_query(query)
        validate_query('SELECT ("SERVICE DELETE FROM" AS ?text) WHERE {}')

    def test_result_limit_and_timeout_are_explicit(self):
        async def run():
            data=fixture();data['entities'][0]['wiki_content']='長い本文'*1000
            nt=snapshot_graph(data).serialize(format='nt')
            result=await execute_query(nt,'SELECT ?text WHERE {:project_1 :紹介本文 ?text}')
            self.assertTrue(result['limited'])
            self.assertEqual(1600,len(result['rows'][0]['text']['value']))
            result=await execute_query(nt,'SELECT * WHERE {?s ?p ?o}',timeout=0.001)
            self.assertFalse(result['ok'])
            self.assertIn('上限',result['error'])
        asyncio.run(run())

