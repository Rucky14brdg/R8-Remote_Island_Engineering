"""実DBを変更せず、ローカルモデルの回答・参照範囲・待ち時間を比較する。
使い方: .venv/bin/python scripts/eval_island_ai.py --models qwen3.5:4b gemma4:12b --output /tmp/ai-eval.json
自動検査は形式・参照範囲だけ。意味の正確さは保存した回答を人が確認する。
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llm_agent import explore_events

CASES = [
    ('nursery-research', '保育所に関する研究について、知っている限りを教えてください', 'search', False),
    ('pickup-research', '送り迎えに関する研究はありますか', 'search', False),
    ('slope-research', '坂道に関する研究を知りたい', 'search', False),
    ('research-list', '離島工学に関する研究はありますか。', 'search', False),
    ('definition-after-research', 'RDFとは何ですか', 'search', True),
    ('research-plans', '離島工学の概念整理とRDFを用いた知識基盤の構築について、実施済みと計画を分けてください', 'search', False),
    ('new-topic', '弓削保育所ではどんな課題がありますか', 'search', True),
    ('reuse', '送迎の問題を解決する研究を他の地域で活用するときの条件は？', 'connect', False),
    ('unknown', '火星の地下都市の建設実績は登録されていますか', 'search', False),
]

async def evaluate(models, output, cases=None):
    results = []
    for model in models:
        history, context_ids = [], []
        for name, question, mode, continuation in CASES:
            if cases and name not in cases:
                continue
            started = time.monotonic()
            first = None
            events = []
            async for event in explore_events(question, model=model, mode=mode,
                    history=history if continuation else [], context_ids=context_ids if continuation else []):
                events.append(event)
                if event['type'] == 'delta' and first is None:
                    first = round(time.monotonic() - started, 2)
            retrieval = next((e for e in events if e['type'] == 'retrieval'), {})
            final = events[-1]
            result = {'case': name, 'model': model, 'question': question,
                      'seconds': round(time.monotonic() - started, 2), 'first_text_seconds': first,
                      'source_titles': [r['title'] for r in retrieval.get('sources', [])],
                      'keywords': retrieval.get('keywords', []), 'result': final}
            results.append(result)
            Path(output).write_text(json.dumps(results, ensure_ascii=False, indent=2))
            print(f"{model} {name}: {result['seconds']}s, {len(result['source_titles'])} sources, {final['type']}", flush=True)
            if final['type'] == 'done':
                history = [{'role': 'user', 'content': question}, {'role': 'assistant', 'content': final['answer']}]
                context_ids = final.get('context_ids', [])
    return results

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--models', nargs='+', default=['qwen3.5:4b', 'gemma4:12b'])
    parser.add_argument('--output', default='/tmp/ritou-ai-eval.json')
    parser.add_argument('--cases', nargs='+', choices=[c[0] for c in CASES])
    args = parser.parse_args()
    asyncio.run(evaluate(args.models, args.output, args.cases))
