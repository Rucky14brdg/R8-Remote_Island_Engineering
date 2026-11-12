"""LLMが読取専用SPARQLを反復し、取得結果から次の検索か回答かを選ぶ。"""
import asyncio
import json
import multiprocessing as mp
import time
from rdflib import Graph, URIRef, Literal, RDF
from rdflib.plugins.sparql.parser import parseQuery
from rdflib.plugins.sparql import prepareQuery
from rdflib.plugins.sparql.parserutils import CompValue
from pyparsing import ParseResults
from rdf_manager import BASE_URI

PREFIXES = f'PREFIX : <{BASE_URI}>\nPREFIX rdf: <{RDF}>\nPREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\nPREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n'
QUERY_TIMEOUT = 5
MAX_ROWS = 40


def uri(value):
    return URIRef(value if str(value).startswith(('http://','https://','urn:')) else BASE_URI+str(value))


def snapshot_graph(data):
    graph=Graph()
    graph.bind('',URIRef(BASE_URI))
    if data.get('triples'):
        turtle='@prefix : <'+BASE_URI+'> .\n@prefix rdf: <'+str(RDF)+'> .\n'
        turtle+='\n'.join(f"{t['subject']} {t['predicate']} {t['object']} ." for t in data['triples'])
        graph.parse(data=turtle,format='turtle')
    else:
        for ent in data.get('entities',[]):
            subject=uri(ent['id'])
            for kind in ent.get('types',[]):graph.add((subject,RDF.type,uri(kind)))
            for key,values in ent.get('literals',{}).items():
                for value in values:graph.add((subject,uri(key),Literal(value)))
            for edge in ent.get('outgoing',[]):graph.add((subject,uri(edge['predicate']),uri(edge['object_id'])))
    for ent in data.get('entities',[]):
        if ent.get('wiki_content'):graph.add((uri(ent['id']),uri('紹介本文'),Literal(ent['wiki_content'])))
    for article in data.get('articles',[]):
        subject=uri(article['id'])
        graph.add((subject,RDF.type,uri('Article')))
        for key,field in [('タイトル','title'),('本文','content'),('日付','date')]:
            if article.get(field):graph.add((subject,uri(key),Literal(article[field])))
        for key,field in [('研究について','related_research'),('関連する','related_entities'),('作成者','authors')]:
            for target in article.get(field,[]):graph.add((subject,uri(key),uri(target)))
    return graph


def validate_query(query):
    if not isinstance(query,str) or not query.strip() or len(query)>10000:
        raise ValueError('クエリは1〜10000文字にしてください')
    parsed=parseQuery(PREFIXES+query)
    if parsed[1].name not in {'SelectQuery','AskQuery','ConstructQuery','DescribeQuery'}:
        raise ValueError('読取専用のSELECT/ASK/CONSTRUCT/DESCRIBEだけを使用できます')
    def inspect(node):
        if isinstance(node,CompValue):
            if node.name in {'ServiceGraphPattern','DatasetClause'}:
                raise ValueError('ローカルDBだけを検索してください。SERVICEとFROMは使用できません')
            for child in node.values():inspect(child)
        elif isinstance(node,(list,tuple,ParseResults)):
            for child in node:inspect(child)
        elif isinstance(node,dict):
            for child in node.values():inspect(child)
    inspect(parsed)
    return PREFIXES+query


def direction_hints(graph,query):
    hints=[];queries=[]
    def inspect(node):
        if isinstance(node,CompValue):
            if node.name=='BGP':
                for subject,predicate,obj in node.get('triples',[]):
                    if not isinstance(predicate,URIRef):continue
                    if isinstance(subject,URIRef) and not any(graph.triples((subject,predicate,None))) and any(graph.triples((None,predicate,subject))):
                        hints.append(f'関係の向き: ?subject <{predicate}> <{subject}> というデータがあります。')
                        queries.append(f'SELECT ?subject WHERE {{?subject {predicate.n3()} {subject.n3()}}} LIMIT 20')
                    if isinstance(obj,URIRef) and not any(graph.triples((None,predicate,obj))) and any(graph.triples((obj,predicate,None))):
                        hints.append(f'関係の向き: <{obj}> <{predicate}> ?object というデータがあります。')
                        queries.append(f'SELECT ?object WHERE {{{obj.n3()} {predicate.n3()} ?object}} LIMIT 20')
            for value in node.values():inspect(value)
        elif isinstance(node,(list,tuple)):
            for value in node:inspect(value)
    try:inspect(prepareQuery(query).algebra)
    except Exception:pass
    return list(dict.fromkeys(hints))[:4],list(dict.fromkeys(queries))[:4]


def _query_worker(nt,query,pipe):
    try:
        graph=Graph().parse(data=nt,format='nt')
        result=graph.query(query)
        ids=set();rows=[];limited=False;size=0
        def cell(value):
            if value is None:return None
            text=str(value)
            if isinstance(value,URIRef) and text.startswith(BASE_URI):ids.add(text[len(BASE_URI):])
            return {'type':'uri' if isinstance(value,URIRef) else 'literal','value':text[:1600],
                    'truncated':len(text)>1600}
        if result.type=='ASK':
            output={'kind':'ASK','boolean':bool(result.askAnswer),'rows':[],'limited':False,'ids':[]}
        else:
            variables=[str(v) for v in result.vars] if result.type=='SELECT' else ['subject','predicate','object']
            iterator=iter(result) if result.type=='SELECT' else iter(result.graph)
            for row in iterator:
                if len(rows)>=MAX_ROWS:
                    limited=True;break
                converted={key:cell(value) for key,value in zip(variables,row)}
                encoded=json.dumps(converted,ensure_ascii=False)
                if size+len(encoded)>22000:
                    limited=True;break
                rows.append(converted);size+=len(encoded)
                limited=limited or any(c and c['truncated'] for c in converted.values())
            output={'kind':result.type,'rows':rows,'limited':limited,'ids':sorted(ids)}
            if not rows:output['hints'],output['suggested_queries']=direction_hints(graph,query)
        pipe.send({'ok':True,**output})
    except Exception as exc:
        pipe.send({'ok':False,'error':str(exc)[:800]})
    finally:pipe.close()


async def execute_query(nt,query,timeout=QUERY_TIMEOUT):
    try:query=validate_query(query)
    except Exception as exc:
        message=str(exc)[:800]
        if isinstance(query,str) and 'UNION' in query.upper():
            message+='\nUNIONは WHERE { {パターン1} UNION {パターン2} } のように外側の波括弧でまとめます。長いUNIONを避け、対象をVALUESで指定するか、一つずつ検索できます。'
        return {'ok':False,'error':message}
    ctx=mp.get_context('spawn');parent,child=ctx.Pipe(duplex=False)
    process=ctx.Process(target=_query_worker,args=(nt,query,child),daemon=True)
    process.start();child.close();started=time.monotonic()
    try:
        while not parent.poll():
            if not process.is_alive():return {'ok':False,'error':'検索処理が終了しました'}
            if time.monotonic()-started>timeout:return {'ok':False,'error':'検索時間の上限に達しました。条件を絞って再検索してください'}
            await asyncio.sleep(0.03)
        return await asyncio.to_thread(parent.recv)
    finally:
        if process.is_alive():process.terminate()
        await asyncio.to_thread(process.join,1)
        if process.is_alive():process.kill();await asyncio.to_thread(process.join,1)
        parent.close();process.close()
