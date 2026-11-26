"""用途別フォームを既存のRDFモデルへ変換する。画面に述語の選択を要求しない。"""
from uuid import uuid4
from urllib.parse import quote
from rdflib import RDF, Graph, Literal, URIRef
import rdf_manager as db


def _name(value):
    return value.strip().lstrip(':')


def _links(values):
    result = []
    for value in values:
        value = value.strip()
        if value and not value.startswith(('https://', 'http://')):
            raise ValueError('関連リンクは http:// または https:// で入力してください')
        if value and value not in result:
            result.append(value)
    return result


def _reference(g, name, cls):
    name = _name(name)
    if not name:
        raise ValueError('項目名を入力してください')
    # 自由に入力した名称の空白・記号を、RDFの識別子へそのまま流さない。
    local_id = name
    existing = next((s for s in g.subjects(RDF.type, None) if str(s) == db.BASE_URI + name), None)
    if existing is None:
        try:
            probe = Graph().parse(data=f'@prefix : <{db.BASE_URI}> . :{name} a :Resource .', format='turtle')
            valid = len(probe) == 1 and str(next(probe.subjects())) == db.BASE_URI + name
        except Exception:
            valid = False
        if not valid:
            local_id = 'named_' + quote(name, safe='').replace('.', '%2E')
    node = db.RITOU[local_id]
    types = set(g.objects(node, RDF.type))
    if types and db.RITOU[cls] not in types:
        raise ValueError(f'「{name}」は別の種類で登録されています。同名の項目を選び直してください')
    g.add((node, RDF.type, db.RITOU[cls]))
    if local_id != name and not list(g.objects(node, db.RITOU['タイトル'])):
        g.add((node, db.RITOU['タイトル'], Literal(name)))
    return node


def _literal(g, node, key, value):
    g.remove((node, db.RITOU[key], None))
    if value.strip():
        g.add((node, db.RITOU[key], Literal(value.strip())))


def _relationships(g, node, predicate, values, cls):
    g.remove((node, db.RITOU[predicate], None))
    for value in dict.fromkeys(_name(v) for v in values if v.strip()):
        g.add((node, db.RITOU[predicate], _reference(g, value, cls)))


def _external_links(g, node, values):
    g.remove((node, db.RITOU['外部リンク'], None))
    for value in _links(values):
        g.add((node, db.RITOU['外部リンク'], URIRef(value)))


def _validate_and_save(g, nodes):
    for node in set(nodes):
        db.validate_entity(g, node)
    db.save_graph(g)


def save_profile(body):
    g = db.load_graph()
    node = _reference(g, body.entity_id, 'User')
    if not db.can_manage_organizations.get():
        display_name = str(g.value(node, db.RITOU['表示名']) or db.node_to_id(node))
        if body.display_name is not None and body.display_name.strip() not in {'', display_name}:
            db.require_organization_admin()
    elif body.display_name is not None:
        _literal(g, node, '表示名', body.display_name)
    for key, value in [('肩書', body.headline), ('概要', body.summary),
                       ('研究キーワード', '\n'.join(body.keywords))]:
        _literal(g, node, key, value)
    for key, tags in [('基本情報タグ', body.basic_tags), ('固定基本情報タグ', body.fixed_basic_tags)]:
        if tags is not None:
            values = dict.fromkeys(tag.strip() for tag in tags if tag.strip())
            _literal(g, node, key, '\n'.join(values))
    if body.affiliations is not None:
        _relationships(g, node, '所属する', body.affiliations, 'Laboratory')
    preference = db.RITOU['活動を表示する所属組織']
    memberships = set(g.objects(node, db.RITOU['所属する']))
    if body.organization_project_ids is not None:
        selected = {db.RITOU[_name(value)] for value in body.organization_project_ids}
        if not selected <= memberships or any((org, RDF.type, db.RITOU.Laboratory) not in g for org in selected):
            raise ValueError('研究・活動の表示対象には現在の所属組織を選択してください')
        g.remove((node, preference, None))
        for org in selected:
            g.add((node, preference, org))
    elif body.affiliations is not None:
        for org in set(g.objects(node, preference)) - memberships:
            g.remove((node, preference, org))
    if body.statuses is not None:
        _relationships(g, node, 'である', body.statuses, 'Status')
    _external_links(g, node, body.external_links)
    saved_id = db.node_to_id(node)
    db.check_wiki_permissions(saved_id, profile_subject=node)
    db.validate_entity(g, node)
    db.save_graph(g, profile_subject=node)
    db.save_wiki_content(saved_id, body.description, profile_subject=node)
    return saved_id


def save_organization(body):
    db.require_organization_admin()
    if not body.name.strip():
        raise ValueError('組織名を入力してください')
    g = db.load_graph()
    existing = set(g.subjects(RDF.type, db.RITOU.Laboratory))
    node = _reference(g, body.entity_id or body.name, 'Laboratory')
    if not body.entity_id and node in existing:
        raise ValueError('同名の組織が登録済みです。組織一覧から編集してください')
    _literal(g, node, 'タイトル', body.name)
    _literal(g, node, '概要', body.summary)
    if body.affiliations is not None:
        _relationships(g, node, '所属する', body.affiliations, 'Laboratory')
    _external_links(g, node, body.external_links)
    touched = [node]
    for ids, cls in ((body.member_ids, db.RITOU.User),
                     (body.child_organization_ids, db.RITOU.Laboratory)):
        if ids is None:
            continue
        membership = db.RITOU['所属する']
        desired = {db.RITOU[_name(value)] for value in ids}
        if any((item, RDF.type, cls) not in g for item in desired):
            raise ValueError('登録済みの人物・組織を選択してください')
        current = {item for item in g.subjects(membership, node)
                   if (item, RDF.type, cls) in g}
        for item in current - desired:
            g.remove((item, membership, node))
        for item in desired - current:
            g.add((item, membership, node))
        touched.extend(current ^ desired)
    _validate_and_save(g, touched)
    saved_id = db.node_to_id(node)
    db.save_wiki_content(saved_id, body.description)
    return saved_id


def save_activity(body):
    if not body.title.strip():
        raise ValueError('研究・活動の名称を入力してください')
    g = db.load_graph()
    # 名称を変更しても、識別子と既存の関係を維持する。
    activity_id = _name(body.entity_id) if body.entity_id else 'research_' + uuid4().hex[:16]
    existing = set(g.subjects(RDF.type, db.RITOU.Research))
    node = _reference(g, activity_id, 'Research')
    is_new = node not in existing
    if body.status is not None or is_new:
        _literal(g, node, '進行状況', body.status or '進行中')
    for key, value in [('タイトル', body.title), ('概要', body.summary), ('実施時期', body.period)]:
        _literal(g, node, key, value)
    for predicate, values, cls in [('対象とする', body.targets, 'Target'),
                                    ('解決を試みる', body.issues, 'Issue'),
                                    ('用いる', body.technologies, 'Technology'),
                                    ('活用する', body.resources, 'Resource')]:
        _relationships(g, node, predicate, values, cls)
    # 従来の技術利用を、新しい述語へ置き換える（他の関係は保持）。
    for value in list(g.objects(node, db.RITOU['扱う'])):
        if (value, RDF.type, db.RITOU.Technology) in g:
            g.remove((node, db.RITOU['扱う'], value))
    _external_links(g, node, body.external_links)
    touched = [node]
    # 他の登録経路の目標を消さず、このフォームで管理する目標だけを更新。
    goal = db.RITOU[activity_id + '_portfolio_goal']
    if body.goal.strip():
        g.add((goal, RDF.type, db.RITOU.Goal))
        _literal(g, goal, '概要', body.goal)
        g.add((node, db.RITOU['目指す'], goal))
        touched.append(goal)
    else:
        g.remove((node, db.RITOU['目指す'], goal))
        # 根拠等から参照される可能性があるので目標自体は削除しない。

    # 省略時は既存の参加者を保持する。画面は読み込んだ参加者一覧を送る。
    if body.participants is not None:
        desired = {}
        for participant in body.participants:
            if participant.actor_class not in {'User', 'Laboratory', 'ActorGroup'}:
                raise ValueError('メンバーの種類が不正です')
            name = _name(participant.name)
            if name in desired:
                raise ValueError('同じメンバーは一つの行にまとめてください')
            if not participant.role.strip():
                raise ValueError('メンバーの役割を入力してください')
            actor = _reference(g, name, participant.actor_class)
            desired[name] = (actor, participant.role.strip())
            touched.append(actor)
        existing = list(g.subjects(db.RITOU['活動'], node))
        by_actor = {}
        for record in existing:
            actor = g.value(record, db.RITOU['主体'])
            if actor:
                by_actor.setdefault(db.node_to_id(actor), []).append(record)
        # 除外した参加者について、活動との関連だけを解除する。根拠記述の
        # 付いた参加記録は履歴として残し、別活動の役割へ影響させない。
        for name, records in by_actor.items():
            if name not in desired:
                if any(list(g.subjects(db.RITOU['記述対象'], record)) for record in records):
                    raise ValueError(f'「{name}」の役割には根拠記録があります。詳細編集で確認してください')
                for record in records:
                    g.remove((record, None, None))
                    g.remove((None, db.RITOU['参加関係'], record))
        for name, (actor, role) in desired.items():
            records = by_actor.get(name, [])
            # 複数の既存役割をひとまとめに上書きしない。
            if len(records) > 1:
                old_roles = '・'.join(dict.fromkeys(str(v) for rec in records for v in g.objects(rec, db.RITOU['役割'])))
                if role != old_roles:
                    raise ValueError(f'「{name}」には複数の役割記録があります。詳細編集で確認してください')
                continue
            record = records[0] if records else db.RITOU['participation_' + uuid4().hex[:16]]
            g.add((record, RDF.type, db.RITOU.Participation))
            g.set((record, db.RITOU['主体'], actor))
            g.set((record, db.RITOU['活動'], node))
            _literal(g, record, '役割', role)
            g.add((node, db.RITOU['参加関係'], record))
            touched.append(record)
        # 従来の担当関係も一覧に含め、除外したものだけを解除。
        for predicate in ['担当する', '扱う']:
            for actor in list(g.subjects(db.RITOU[predicate], node)):
                if db.node_to_id(actor) not in desired:
                    g.remove((actor, db.RITOU[predicate], node))
    db.check_wiki_permissions(activity_id)
    _validate_and_save(g, touched)
    db.save_wiki_content(activity_id, body.description)
    return activity_id
