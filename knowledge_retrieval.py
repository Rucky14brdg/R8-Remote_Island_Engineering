"""DBを読み、自然文の手掛かりと実際のRDF関係から参照元を組み立てる。"""
import re
import unicodedata
from collections import defaultdict, deque
from urllib.parse import quote

PRIMARY_TYPES = {'Research', 'Issue', 'Technology', 'Resource', 'Target', 'User', 'Laboratory', 'ActorGroup'}
SUPPORT = {'根拠記述', '出典', '原資料', '原記事', '参加関係', '主体', '活動', '目指す', '実績を持つ',
           '参照資料', '記述対象', '関係先', '記録者', '作成者'}
STOPWORDS = {'研究', '活動', '技術', '情報', '資料', '事例', '方法', '一覧', '比較', '関連', '内容', '質問',
             '何が', '教えて', '知りたい', '探して', '考えて', 'について', 'できる', 'あります', 'ください', '活用'}


def normalized(value):
    return unicodedata.normalize('NFKC', str(value)).casefold()


def usable_term(value):
    return bool(value and normalized(value) not in STOPWORDS and
                (len(value) >= 2 or re.fullmatch(r'[一-龥々]', value)))


def entity_title(entity):
    literals = entity.get('literals', {})
    return (literals.get('表示名') or literals.get('タイトル') or [entity['id']])[0]


def reference_title(entity, by_id):
    title = entity_title(entity)
    if title != entity['id']:
        return title
    if 'Participation' in entity.get('types', []):
        actor_id = next((e['object_id'] for e in entity.get('outgoing', []) if e['predicate'] == '主体'), '')
        actor = entity_title(by_id[actor_id]) if actor_id in by_id else '参加者'
        role = (entity.get('literals', {}).get('役割') or ['役割'])[0]
        return f'{actor}の参加記録（{role}）'
    if 'Evidence' in entity.get('types', []):
        kind = (entity.get('literals', {}).get('記述区分') or ['記述'])[0]
        return kind + 'の出典付き記述'
    return title


def entity_href(entity):
    kind = set(entity.get('types', []))
    prefix = 'activity' if 'Research' in kind else 'person' if 'User' in kind else 'article' if 'Article' in kind else 'entity'
    return '#' + prefix + '/' + quote(entity['id'], safe='')


def query_terms(question, all_data=None):
    terms = [question.strip()]
    registered_names = {entity_title(e) for e in (all_data or {}).get('entities', []) if set(e.get('types', [])).intersection(PRIMARY_TYPES)}
    terms += re.findall(r'[一-龥々]{2,}|[ァ-ヴー]{2,}|[a-zA-Z0-9_+#.\-]{2,}', question)
    if all_data:
        for ent in all_data.get('entities', []):
            if not set(ent.get('types', [])).intersection(PRIMARY_TYPES):
                continue
            for name in [ent['id'], entity_title(ent)]:
                if usable_term(name) and normalized(name) in normalized(question):
                    terms.append(name)
    return list(dict.fromkeys(t.strip()[:150] for t in terms if usable_term(t.strip()) or t.strip() in registered_names))[:24]


def _excerpt(text, keywords, limit=1400):
    text = str(text or '').strip()
    if len(text) <= limit:
        return text
    positions = [normalized(text).find(normalized(k)) for k in keywords if len(k) >= 2]
    positions = [p for p in positions if p >= 0]
    start = max(0, min(positions) - 180) if positions else 0
    if start and text[start - 1] != '\n':
        boundary = text.find('\n', start, start + 90)
        if boundary >= 0:
            start = boundary + 1
    return ('〔途中から抜粋〕\n' if start else '') + text[start:start + limit] + '\n〔本文の一部を省略〕'


def search_subgraph_and_docs(keywords, all_data, context_ids=None, limit=56):
    entities = [e for e in all_data.get('entities', []) if not e.get('is_external') and e.get('types')]
    by_id = {e['id']: e for e in entities}
    articles = all_data.get('articles', [])
    article_ids = {a['id'] for a in articles}
    registered_names = {normalized(entity_title(e)) for e in entities if set(e.get('types', [])).intersection(PRIMARY_TYPES)}
    kws = list(dict.fromkeys(normalized(k.strip().lstrip(':')) for k in keywords if k and (usable_term(k.strip()) or normalized(k.strip()) in registered_names)))[:32]
    scores = {}
    for e in entities:
        if not set(e.get('types', [])).intersection(PRIMARY_TYPES):
            continue
        name = normalized(entity_title(e))
        text = normalized(e.get('wiki_content', '') + '\n' + '\n'.join(v for k, vs in e.get('literals', {}).items() if k not in {'記事版', 'ファイルパス', 'Wikiパス'} for v in vs))
        score = sum((9 if kw == name else 0 if kw in STOPWORDS else 5 if kw in name else 1.5 if kw in text else 0) for kw in kws)
        if score:
            scores[e['id']] = score
    article_scores = {}
    for a in articles:
        name = normalized(a['title'])
        body = normalized(a.get('content', ''))
        score = sum(5 if kw == name else 0 if kw in STOPWORDS else 5 if kw in name else 1.5 if kw in body else 0 for kw in kws)
        if score:
            article_scores[a['id']] = score
    seeds = [key for key, _ in sorted(scores.items(), key=lambda x: (-x[1], x[0]))[:10]]
    seeds += [i for i in (context_ids or []) if i in by_id or i in article_ids]
    matched_articles = set(key for key, _ in sorted(article_scores.items(), key=lambda x: (-x[1], x[0]))[:8])
    for a in articles:
        if a['id'] in matched_articles or a['id'] in seeds:
            seeds.extend(a.get('related_research', []) + a.get('related_entities', []))
            matched_articles.add(a['id'])
    # 「登録されている研究の一覧」など、全体を尋ねる問いのみ一覧を入口にする。
    if not seeds and any(word in ' '.join(keywords) for word in ['一覧', '何が登録', 'どんな研究', 'どのような研究']):
        seeds += [e['id'] for e in entities if 'Research' in e.get('types', [])][:10]
    seeds = list(dict.fromkeys(seeds))[:16]
    adjacency = defaultdict(list)
    all_edges = []
    for e in entities:
        for edge in e.get('outgoing', []):
            other = edge['object_id']
            if other not in by_id:
                continue
            triple = (e['id'], edge['predicate'], other)
            all_edges.append(triple)
            adjacency[e['id']].append((other, triple))
            adjacency[other].append((e['id'], triple))
    selected = set(i for i in seeds if i in by_id)
    distance = {i: 0 for i in selected}
    queue = deque(sorted(selected, key=lambda i: (-scores.get(i, 0), i)))
    # 起点から実際に結ばれた項目を二段階たどる。所属等の大きなハブで全DBへ拡散しない。
    while queue and len(selected) < limit:
        current = queue.popleft()
        if distance[current] >= 2:
            continue
        for other, triple in sorted(adjacency[current], key=lambda pair: (pair[1][1], pair[0])):
            if triple[1] in {'記録者', '作成者', '所属する', 'である', '外部リンク'}:
                continue
            if other not in selected:
                selected.add(other)
                distance[other] = distance[current] + 1
                queue.append(other)
                if len(selected) >= limit:
                    break
    # 研究→実績→根拠記述→出典→元記事を伴わせる。直接一致しない資料も欠落させない。
    frontier = set(selected)
    support_limited = False
    for _ in range(4):
        extra = set()
        for eid in sorted(frontier):
            for edge in by_id.get(eid, {}).get('outgoing', []):
                if edge['predicate'] in SUPPORT and edge['object_id'] in by_id:
                    if len(selected | extra) >= limit + 50:
                        support_limited = True
                        break
                    extra.add(edge['object_id'])
        extra -= selected
        if not extra:
            break
        selected.update(extra)
        frontier = extra
        if len(selected) > limit + 50:
            break
    project_ids = {i for i in selected if 'Research' in by_id[i].get('types', [])}
    for a in articles:
        related = set(a.get('related_research', []) + a.get('related_entities', []))
        if a['id'] in selected or project_ids.intersection(related):
            matched_articles.add(a['id'])
    ranked_articles = sorted((a for a in articles if a['id'] in matched_articles),
                             key=lambda a: (-article_scores.get(a['id'], 0), a['id']))[:10]
    order = {'Research': 0, 'Evidence': 1, 'Result': 2, 'Goal': 3, 'Source': 4, 'Participation': 5}
    result_entities = sorted((by_id[i] for i in selected if i not in article_ids),
        key=lambda e: (order.get(e.get('types', [''])[0], 6), -scores.get(e['id'], 0), e['id']))
    edges = [edge for edge in all_edges if edge[0] in selected and edge[2] in selected]
    relations = [f':{s} --[:{p}]--> :{o}' for s, p, o in edges]
    paths = []
    for s, p, o in edges:
        if p in {'根拠記述', '記述対象', '記録者', '作成者', '出典', '原資料', '原記事', '研究について'}:
            continue
        if not set(by_id[s].get('types', [])).intersection(PRIMARY_TYPES | {'Participation', 'Goal', 'Result'}):
            continue
        paths.append({'subject': {'id': s, 'title': reference_title(by_id[s], by_id), 'href': entity_href(by_id[s])},
                      'predicate': p, 'object': {'id': o, 'title': reference_title(by_id[o], by_id), 'href': entity_href(by_id[o])}})
    return {'entities': result_entities, 'articles': ranked_articles, 'relations': list(dict.fromkeys(relations)),
            'paths': paths[:12], 'keywords': keywords, 'seeds': seeds,
            'limited': support_limited or len(selected) >= limit or len(matched_articles) > 10}


def _entity_context(ent, retrieved, keywords):
    by_id = {e['id']: e for e in retrieved['entities']}
    lines = [f"{reference_title(ent, by_id)} / 種類: {', '.join(ent.get('types', []))}"]
    participation = retrieved.get('research_participations', {}).get(ent['id'])
    if 'Research' in ent.get('types', []):
        lines.append('この研究の参加者・役割（RDFの研究別参加記録）:')
        if not participation or not participation.get('checked'):
            lines.append('今回未確認。記事の著者・所属から参加者を補わない。')
        else:
            for member in participation['members']:
                lines.append(f"- {member['actor_name']}: {member['role']}")
            if not participation['members']:
                lines.append('今回のRDF参加記録では参加者・役割を確認できません。')
            if participation.get('limited'):
                lines.append('参加記録は取得上限により一部のみ確認。')
    for key, values in ent.get('literals', {}).items():
        if key in {'Wikiパス', 'ファイルパス', '記事版'}:
            continue
        value = ' / '.join(values)
        lines.append(f'{key}: ' + _excerpt(value, keywords, 1800 if key in {'値', '記述内容'} else 900))
    if ent.get('wiki_content'):
        lines.append('紹介本文:\n' + _excerpt(ent['wiki_content'], keywords, 1600))
    for edge in ent.get('outgoing', []):
        if edge['predicate'] == '参加関係' and participation:
            continue
        target = by_id.get(edge['object_id'])
        name = reference_title(target, by_id) if target else edge['object_id']
        lines.append(f"関係: {edge['predicate']} → {name}")
        if edge['predicate'] in {'出典', '原資料'} and target:
            lines.append('資料名: ' + entity_title(target))
            lines.append('資料の発行時点: ' + ' / '.join(target.get('literals', {}).get('発行時点', [])))
    return '\n'.join(lines)


def build_references(retrieved, budget=19000):
    refs, blocks, used = [], [], 0
    keywords = retrieved.get('keywords', [])
    candidates = []
    for ent in retrieved['entities']:
        types = ent.get('types', [])
        # 入力記録や記事版の本文はEvidence側の引用を優先し、同じ長文を重ねて投入しない。
        if 'Source' in types and (ent.get('literals', {}).get('資料区分') or [''])[0] in {'入力記録', '記事記録'}:
            ent = {**ent, 'literals': {k: v for k, v in ent.get('literals', {}).items() if k != '説明'}}
        context = _entity_context(ent, retrieved, keywords)
        literals = ent.get('literals', {})
        excerpt = (literals.get('記述内容') or literals.get('値') or literals.get('概要') or literals.get('説明') or [ent.get('wiki_content', '')])[0]
        candidates.append(({'id': ent['id'], 'title': reference_title(ent, {e['id']: e for e in retrieved['entities']}), 'kind': types[0] if types else 'Entity',
            'kind_label': ent.get('type_info', {}).get('short', types[0] if types else '項目'),
            'href': entity_href(ent), 'excerpt': _excerpt(excerpt, keywords, 320),
            'claim_kind': (literals.get('記述区分') or literals.get('種別') or [''])[0],
            'confirmation': (literals.get('確認状況') or [''])[0],
            'period': (literals.get('活動時期') or literals.get('実施時期') or literals.get('発行時点') or [''])[0],
            'locator': (literals.get('該当箇所') or [''])[0][:400]}, context))
    # 記事本文も必ず独立した参照元として扱う。研究側の関係を本文中の発言と混ぜない。
    for a in retrieved['articles']:
        excerpt = _excerpt(a.get('content', ''), keywords, 2100)
        by_id = {e['id']: e for e in retrieved['entities']}
        names = lambda ids: ', '.join(entity_title(by_id[key]) if key in by_id else key for key in ids)
        context = f"記事: {a['title']} / 種類: {a['article_type']}\n公開日: {a.get('date', '')}\nこの記事を書いた人（著者）: {names(a.get('authors', []))}\n著者登録は記事の執筆者を表し、関連研究への参加を意味しません。\n関連研究: {names(a.get('related_research', []))}\n本文:\n{excerpt}"
        for research_id in a.get('related_research', []):
            participation = retrieved.get('research_participations', {}).get(research_id, {})
            if participation.get('checked') and not participation.get('limited'):
                for author in a.get('authors', []):
                    roles = [m['role'] for m in participation['members'] if m['actor_id'] == author]
                    status = '登録された役割: ' + '・'.join(roles) if roles else '今回の研究別参加記録では参加を確認できません'
                    context += f"\n著者 {names([author])} と研究 {names([research_id])} の関係: {status}"
        candidates.append(({'id': a['id'], 'title': a['title'], 'kind': 'Article', 'kind_label': '記事',
            'href': '#article/' + quote(a['id'], safe=''), 'excerpt': _excerpt(a.get('content', ''), keywords, 320),
            'claim_kind': a['article_type'], 'confirmation': '', 'period': a.get('date', ''), 'locator': ''}, context))
    # 記事が最後尾の情報量制限で全部落ちないよう、研究→記事→根拠→その他の順にする。
    priority = {'Research': 0, 'Article': 1, 'Evidence': 2, 'Result': 3, 'Goal': 4, 'Source': 5}
    groups = defaultdict(deque)
    for candidate in candidates:
        groups[priority.get(candidate[0]['kind'], 6)].append(candidate)
    candidates = []
    while any(groups.values()):
        for key in sorted(groups):
            if groups[key]:
                candidates.append(groups[key].popleft())
    omitted = False
    for ref, context in candidates:
        block = f"【参照{len(refs) + 1}】\n{context}\n"
        if used + len(block) > budget:
            omitted = True
            continue
        ref['number'] = len(refs) + 1
        refs.append(ref)
        blocks.append(block)
        used += len(block)
        if len(refs) >= 32:
            omitted = len(candidates) > len(refs)
            break
    return refs, '\n'.join(blocks), omitted


def format_retrieval_context(retrieved):
    return build_references(retrieved)[1]


def read_article_reference(article_id, all_data, max_chars=14000):
    """指定された記事を読む。関連研究や著者の他の研究へは展開しない。"""
    article = next((a for a in all_data.get('articles', []) if a['id'] == article_id), None)
    if article is None:
        raise ValueError('対象の記事が見つかりません。記事を開き直してください。')
    content = article.get('content', '').strip()
    if not content:
        raise ValueError('この記事には、読める本文がまだ登録されていません。')
    limited = len(content) > max_chars
    body = content[:max_chars]
    if limited:
        body += '\n〔ここから先の本文は今回の読解範囲に含まれていません〕'
    by_id = {e['id']: e for e in all_data.get('entities', [])}
    authors = [entity_title(by_id[key]) if key in by_id else key for key in article.get('authors', [])]
    reference = {'number': 1, 'id': article_id, 'title': article['title'], 'kind': 'Article',
                 'kind_label': '記事本文', 'href': '#article/' + quote(article_id, safe=''),
                 'excerpt': _excerpt(content, [], 320), 'claim_kind': article.get('article_type', ''),
                 'confirmation': '', 'period': article.get('date', ''), 'locator': '本文（冒頭からの一部）' if limited else '本文全体'}
    context = (f"【参照1：今回読んでいる記事】\nタイトル: {article['title']}\n公開日: {article.get('date', '')}\n"
               f"記事の著者（研究の参加者を意味しない）: {', '.join(authors) or '未設定'}\n"
               f"読解範囲: {reference['locator']}\n\n記事本文:\n{body}")
    return [reference], context, limited
