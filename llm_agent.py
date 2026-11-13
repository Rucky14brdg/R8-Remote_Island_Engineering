"""Gemini版の探索を、参照元・対話履歴・ローカル生成に対応させた実装。"""
import asyncio
import json
import os
import re
from typing import Any, Dict, List

import httpx
from rdf_manager import get_all_data
from knowledge_search import search_events
from knowledge_retrieval import (PRIMARY_TYPES, build_references, read_article_reference,
                                 search_subgraph_and_docs, format_retrieval_context)  # 既存呼び出しとの互換性

OLLAMA_BASE_URL = os.environ.get('OLLAMA_BASE_URL', 'http://127.0.0.1:11434').rstrip('/')
DEFAULT_MODEL = 'qwen3.5:4b'


def _is_local_model(item):
    name = item.get('name', '').lower()
    return bool(name and not item.get('remote_host') and not item.get('remote_model')
                and ':cloud' not in name and not name.endswith('-cloud') and item.get('size', 0) > 0)


def _is_generator(item):
    caps = item.get('capabilities', [])
    return 'completion' in caps if caps else 'embed' not in item.get('name', '').lower()


async def check_ollama_status(model: str = DEFAULT_MODEL) -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=4.0, trust_env=False) as client:
            response = await client.get(f'{OLLAMA_BASE_URL}/api/tags')
            response.raise_for_status()
            items = response.json().get('models', [])
            local = [item for item in items if _is_local_model(item) and _is_generator(item)]
            names = [item['name'] for item in local]
            preferred = model if model in names else DEFAULT_MODEL if DEFAULT_MODEL in names else next(iter(names), '')
            return {'running': True, 'models': names, 'target_model': model, 'available': model in names,
                    'recommended_model': preferred, 'local_only': True,
                    'model_details': [{'name': item['name'], 'size': item.get('size', 0),
                                       'capabilities': item.get('capabilities', [])} for item in local]}
    except (httpx.HTTPError, ValueError, TypeError):
        return {'running': False, 'models': [], 'target_model': model, 'available': False,
                'recommended_model': '', 'local_only': True, 'error': 'Ollamaに接続できません。起動してから再確認してください。'}


async def call_ollama(messages: List[Dict[str, str]], model: str = DEFAULT_MODEL, think: bool = False,
                      json_mode=False) -> str:
    payload = {'model': model, 'messages': messages, 'think': think, 'stream': False,
               'options': {'temperature': 0.1, 'num_predict': 900 if json_mode else 1600, 'num_ctx': 16384}}
    if json_mode:
        payload['format'] = 'json'
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=4.0), trust_env=False) as client:
        response = await client.post(f'{OLLAMA_BASE_URL}/api/chat', json=payload)
        response.raise_for_status()
        body = response.json()
        if body.get('error'):
            raise ValueError(body['error'])
        return body.get('message', {}).get('content', '')


def answer_prompt(context, mode=None):
    return f"""あなたは離島工学AIです。ユーザーの質問や相談に、日本語で自然に答えてください。
必要に応じて一般知識も使えます。以下の登録情報は参考資料です。資料に基づく説明と、一般的な説明や推測が混同されないようにしてください。参照番号は資料の確認に使えます。
研究の参加者・役割は、その研究の参加記録で確認してください。記事の著者や共通の所属組織から研究への参加を推定しないでください。
<reference_data>
{context or '今回の検索で関連する登録情報は見つかりませんでした。'}
</reference_data>"""


def article_reading_prompt(context, mode=None, limited=False):
    notice = '参考資料は記事本文の一部です。' if limited else '参考資料は選択された記事です。'
    return answer_prompt(context) + '\n' + notice


async def stream_ollama(messages, model, *, max_tokens=1800):
    payload = {'model': model, 'messages': messages, 'think': False, 'stream': True,
               'options': {'temperature': 0.2, 'num_predict': max_tokens, 'num_ctx': 16384}}
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=4.0), trust_env=False) as client:
        async with client.stream('POST', f'{OLLAMA_BASE_URL}/api/chat', json=payload) as response:
            response.raise_for_status()
            done = False
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                item = json.loads(line)
                if item.get('error'):
                    raise ValueError(item['error'])
                text = item.get('message', {}).get('content', '')
                if text:
                    yield {'text': text}
                if item.get('done'):
                    done = True
                    yield {'done': True, 'reason': item.get('done_reason', 'stop')}
                    break
            if not done:
                raise ValueError('回答の受信が途中で終了しました')


async def explore_events(question, model=DEFAULT_MODEL, history=None, mode='search', context_ids=None,
                         scope_id='', scope_mode='auto'):
    history = [m for m in (history or [])[-8:] if m.get('role') in {'user', 'assistant'} and isinstance(m.get('content'), str)]
    history = [{'role': m['role'], 'content': m['content'][:2500]} for m in history]
    for message in history:
        if message['role'] == 'assistant':
            # 過去の回答の番号は今回の参照番号とは別。引き継いで誤参照させない。
            message['content'] = re.sub(r'\[\d+\]', '', message['content'])
    status = await check_ollama_status(model)
    if not status['running']:
        yield {'type': 'error', 'code': 'offline', 'message': status['error']}
        return
    if not status['available']:
        yield {'type': 'error', 'code': 'model_unavailable', 'message': 'このモデルはローカルの生成用モデルとして利用できません。モデルを選び直してください。'}
        return
    yield {'type': 'status', 'phase': 'search', 'message': '質問に合う知識の入口を探しています'}
    try:
        all_data = get_all_data()
    except Exception:
        yield {'type': 'error', 'code': 'data_unavailable', 'message': '登録情報を読み込めませんでした。データベースの状態を確認してください。'}
        return
    article_ids = {a['id'] for a in all_data.get('articles', [])}
    # 起点と会話で見つかった候補を別に扱う。記事だけを読む際は候補も履歴も渡さない。
    anchor = scope_id or next(iter(context_ids or []), '')
    reading = scope_mode != 'related' and anchor in article_ids
    if scope_mode == 'document' and not reading:
        yield {'type': 'error', 'code': 'article_unavailable', 'message': '読む対象の記事を指定してください。記事を開き直して質問できます。'}
        return
    if reading:
        yield {'type': 'status', 'phase': 'read', 'message': '対象の記事本文を読んでいます'}
        try:
            refs, context, omitted = read_article_reference(anchor, all_data)
        except ValueError as exc:
            yield {'type': 'error', 'code': 'article_unavailable', 'message': str(exc)}
            return
        retrieved = {'paths': [], 'limited': omitted}
        keywords, fallback = [], False
        response_scope = 'article'
    else:
        anchors=list(dict.fromkeys(([scope_id] if scope_id else [])+(context_ids or [])))
        search_result=None
        try:
            async for event in search_events(question, all_data, model, history, anchors, call_ollama):
                if event['type']=='search_result':
                    search_result=event
                else:
                    yield event
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            yield {'type':'error','code':'search_failed','message':'検索処理を完了できませんでした。接続を確認して再試行してください。'}
            return
        retrieved=search_result['retrieved']
        refs, context, omitted=build_references(retrieved,budget=10000)
        # 空の検索や候補一覧を、読めた資料の参照元には数えない。
        query_context=[]
        for step in search_result['steps']:
            result=step['result']
            if step.get('operation')!='get_related' or not result.get('ok') or not result.get('items'):
                continue
            number=len(refs)+1
            facts=[{k:item[k] for k in ('name','types','routes','root_names') if k in item} for item in result['items']]
            for fact, item in zip(facts, result['items']):
                if item.get('memberships'):
                    fact['研究別参加記録']=[{k:m[k] for k in ('research_name','actor_name','role','source')} for m in item['memberships']]
            result_text=json.dumps(facts,ensure_ascii=False)
            if len(result_text)>1800:
                result_text=result_text[:1800]+'〔取得結果の一部を省略〕';omitted=True
            query_context.append(f"【参照{number}：関係の検索結果】\n操作: {step['purpose']}\n結果: {result_text}\n")
            refs.append({'number':number,'id':f"search:{step['index']}",'title':step['purpose'],
                         'kind':'QueryResult','kind_label':'関係の検索結果','href':'#ai',
                         'excerpt':'、'.join(item['name'] for item in result['items'])[:400],
                         'claim_kind':'SPARQL実行結果','confirmation':'','period':'','locator':''})
        context+='\n'+'\n'.join(query_context)
        finish=search_result['finish_reason']
        explanations={
            'sufficient':'取得した項目・資料の内容を確認して回答に進みます。',
            'not_needed':'検索する登録情報の話題が指定されていないため、一般的な回答を生成します。',
            'partial':'候補の一部だけ本文を確認できています。本文を確認していない項目は、登録名・概要の範囲に限られます。',
            'no_match':'今回の検索条件では、指定した種類の関連項目は見つかっていません。別種類の参考資料がある場合は、その範囲を区別します。DB全体に存在しないことは意味しません。',
            'tool_error':'検索操作に失敗し、必要な資料を確認できていません。該当するデータがないという意味ではありません。',
            'limit':'検索・判断の上限に達し、調査は完了していません。確認できた範囲を示します。'}
        context+='\n検索の状態: '+finish+'\n'+explanations.get(finish,'')
        if finish in {'limit','tool_error'}:
            yield {'type':'status','phase':'limit','message':explanations[finish]}
        keywords, fallback=[], False
        response_scope='related' if refs else 'general'
    yield {'type': 'retrieval', 'sources': refs, 'paths': retrieved['paths'], 'keywords': keywords,
           'limited': retrieved['limited'] or omitted, 'search_fallback': fallback,
           'response_scope': response_scope, 'scope_id': anchor if reading else scope_id}
    yield {'type': 'status', 'phase': 'generate', 'message': '回答を生成しています'}
    if reading:
        prompt = article_reading_prompt(context, mode, omitted)
        # 以前の誤った回答や横断検索の情報で、記事本文を汚染させない。
        messages = [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': question}]
    else:
        messages = [{'role': 'system', 'content': answer_prompt(context, mode)}] + history + [{'role': 'user', 'content': question}]
    if not reading:
        history_for_answer=[{'role':m['role'],'content':m['content'][:1500]} for m in history[-4:]]
        messages=[messages[0]]+history_for_answer+[{'role':'user','content':question}]
    answer, truncated = '', False
    try:
        async for piece in stream_ollama(messages, model, max_tokens=4096):
            if piece.get('text'):
                answer += piece['text']
                yield {'type': 'delta', 'text': piece['text']}
            if piece.get('done'):
                truncated = piece.get('reason') == 'length'
        if not answer.strip():
            raise ValueError('モデルが回答本文を返しませんでした')
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        code = 'timeout' if isinstance(exc, httpx.TimeoutException) else 'generation_failed'
        message = ('ローカルモデルの応答が時間内に完了しませんでした。質問を短くするか、別のモデルで再試行してください。'
                   if code == 'timeout' else 'ローカルモデルの回答を受信できませんでした。接続を確認して再試行してください。')
        yield {'type': 'error', 'code': code, 'message': message, 'partial': bool(answer)}
        return
    numbers = {r['number'] for r in refs}
    cited = sorted({int(n) for n in re.findall(r'\[(\d+)\]', answer) if int(n) in numbers})
    unknown = sorted({int(n) for n in re.findall(r'\[(\d+)\]', answer) if int(n) not in numbers})
    context_ids = [anchor] if reading else [r['id'] for r in refs if r['kind'] in PRIMARY_TYPES][:8]
    yield {'type': 'done', 'model': model, 'answer': answer, 'cited': cited, 'context_ids': context_ids,
           'truncated': truncated, 'unverified_citations': unknown, 'no_matches': not reading and search_result['finish_reason']=='no_match',
           'response_scope': response_scope, 'scope_id': anchor if reading else scope_id,
           'search_finish': None if reading else search_result['finish_reason'],
           'search_decision': None if reading else search_result['decision'],
           'search_steps': [] if reading else search_result['steps'],
           'search_targets': [] if reading else search_result['targets'],
           'search_read_ids': [] if reading else search_result['read_ids']}


async def explore_knowledge_base(question: str, model: str = DEFAULT_MODEL, **kwargs) -> Dict[str, Any]:
    result = {'ok': False, 'model': model, 'answer': '', 'sources': [], 'paths': []}
    async for event in explore_events(question, model=model, **kwargs):
        if event['type'] == 'retrieval':
            result.update({key: event[key] for key in ['sources', 'paths', 'limited']})
        elif event['type'] == 'delta':
            result['answer'] += event['text']
        elif event['type'] == 'done':
            result.update({key: value for key, value in event.items() if key != 'type'})
            result['ok'] = True
        elif event['type'] == 'error':
            result['answer'] = event['message']
            result['error'] = event['code']
    return result
