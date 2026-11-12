import re
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from rdflib import Graph, Namespace, URIRef, Literal, RDF, RDFS

BASE_URI = "https://example.org/ritou/"
RITOU = Namespace(BASE_URI)
RDF_CLASS_URI = URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#Class")
RDFS_CLASS_URI = RDFS.Class

DATA_DIR = Path(__file__).parent / "data"
TTL_PATH = DATA_DIR / "knowledge.ttl"
ARTICLES_DIR = DATA_DIR / "articles"
WIKI_DIR = DATA_DIR / "wiki"

# スキーマ（設計）と事例データを別ファイルで管理する。
SCHEMA_PATH = DATA_DIR / "schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
CLASS_INFO = SCHEMA["classes"]

# 述語（関係）を一般人向けに自然な日本語ラベルに変換する辞書
PREDICATE_NATURAL_LABELS = {
    "原記事": {"outgoing": "元の記事", "incoming": "この記事をもとにした資料記録"},
    "参照資料": {"outgoing": "根拠資料・参考資料", "incoming": "この資料を参照する研究・活動"},
    "記録者": {"outgoing": "記録した人", "incoming": "この人が登録した記録"},
    "記録先": {"outgoing": "活動記録を残すポートフォリオ", "incoming": "この個人・組織の活動記録"},
    "研究について": {"outgoing": "取り上げている研究・活動", "incoming": "この研究・活動を取り上げた記事"},
    "用いる": {"outgoing": "用いる技術・手法", "incoming": "この技術・手法を用いる研究・活動"},
    "担当する": {"outgoing": "担当する研究・活動", "incoming": "担当する主体"},
    "記述対象": {"outgoing": "この記述が説明する項目", "incoming": "この項目についての出典付き記述"},
    "関係先": {"outgoing": "記述された関係の相手", "incoming": "この項目を関係先とする記述"},
    "活用する": {"outgoing": "活用する資源", "incoming": "この資源を活用する研究・活動"},
    "目指す": {"outgoing": "目標・計画", "incoming": "この目標を目指す研究・活動"},
    "実績を持つ": {"outgoing": "報告された実績・評価", "incoming": "実績のある研究・活動"},
    "根拠記述": {"outgoing": "出典付きの記述", "incoming": "この記述が説明する項目"},
    "出典": {"outgoing": "根拠となる資料", "incoming": "この資料に基づく記述"},
    "原資料": {"outgoing": "卒論が参照した原資料", "incoming": "原資料を参照する記述"},
    "参加関係": {"outgoing": "活動内の役割分担", "incoming": "役割を持つ研究・活動"},
    "主体": {"outgoing": "役割を担う主体", "incoming": "この主体が担う活動内の役割"},
    "活動": {"outgoing": "関与する活動", "incoming": "この活動への参加記録"},
    "構成要素": {"outgoing": "組み合わせる装置・資源", "incoming": "この資源を構成に含む項目"},
    "加工先": {"outgoing": "加工して得る資源", "incoming": "加工前の資源"},
    "用途": {"outgoing": "利用する用途", "incoming": "この用途で使う資源"},
    "抱える": {"outgoing": "抱えている地域課題", "incoming": "この課題を抱えている地域・現場"},
    "対象とする": {"outgoing": "対象としている地域・現場", "incoming": "この地域・現場を対象とする研究・実践"},
    "解決を試みる": {"outgoing": "解決を試みている課題", "incoming": "この課題の解決を試みる研究・実践"},
    "扱う": {"outgoing": "利用技術 / 担当している研究", "incoming": "利用されている研究 / 担当メンバー"},
    "所属する": {"outgoing": "所属している研究室・組織", "incoming": "所属メンバー"},
    "である": {"outgoing": "身分・立場", "incoming": "該当する人物"},
    "関連する": {"outgoing": "関連する項目", "incoming": "関連している記事・項目"},
    "作成者": {"outgoing": "執筆・作成者", "incoming": "執筆した記事"},
    "外部リンク": {"outgoing": "関連リンク（GitHub・外部サイト等）", "incoming": "リンク元の項目"},
}

LITERAL_PREDICATES = set(SCHEMA["attributes"])


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    if not TTL_PATH.exists():
        TTL_PATH.write_text(
            "@prefix : <https://example.org/ritou/> .\n"
            "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n\n"
            "# =========================\n"
            "# クラス\n"
            "# =========================\n\n"
            ":Issue a rdf:Class .\n"
            ":Research a rdf:Class .\n"
            ":Target a rdf:Class .\n"
            ":Technology a rdf:Class .\n"
            ":Laboratory a rdf:Class .\n"
            ":User a rdf:Class .\n"
            ":Status a rdf:Class .\n"
            ":Article a rdf:Class .\n"
            ":ExternalLink a rdf:Class .\n\n"
            "# =========================\n"
            "# ユーザ\n"
            "# =========================\n\n"
            ":石橋治樹\n"
            "    a :User .\n",
            encoding="utf-8",
        )


def is_class_uri(node) -> bool:
    return node in (RDF_CLASS_URI, RDFS_CLASS_URI)


def load_graph() -> Graph:
    ensure_dirs()
    g = Graph()
    g.bind("", RITOU)
    g.bind("rdf", RDF)
    g.parse(str(TTL_PATH), format="turtle")
    return g


def node_to_id(node) -> str:
    if isinstance(node, URIRef):
        s = str(node)
        if s.startswith(BASE_URI):
            return s[len(BASE_URI):]
        return s
    return str(node)


def format_turtle_term(node) -> str:
    if node == RDF.type:
        return "a"
    if is_class_uri(node):
        return "rdf:Class"
    if isinstance(node, URIRef):
        s = str(node)
        if s.startswith(BASE_URI):
            local = s[len(BASE_URI):]
            return f":{local}"
        return f"<{s}>"
    elif isinstance(node, Literal):
        return node.n3()
    return str(node)


def str_to_node(val: str, is_object: bool = False, predicate_name: str = ""):
    val = val.strip()
    if val.startswith("<") and val.endswith(">"):
        return URIRef(val[1:-1])
    if val.startswith("http://") or val.startswith("https://"):
        return URIRef(val)
    if is_object and (
        predicate_name in LITERAL_PREDICATES
        or (val.startswith('"') and val.endswith('"'))
    ):
        clean_val = val[1:-1] if (val.startswith('"') and val.endswith('"')) else val
        return Literal(clean_val)
    if val == "a" or val == "rdf:type":
        return RDF.type
    if val in ("rdf:Class", "rdfs:Class"):
        return RDF_CLASS_URI
    if val.startswith(":"):
        val = val[1:]
    if is_object and ("\n" in val or (" " in val and len(val) > 40)):
        return Literal(val)
    return URIRef(BASE_URI + val)


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|\s]+', "_", name.strip().lstrip(":"))
    return cleaned or "unnamed"


def serialize_clean_turtle(g: Graph) -> str:
    lines = [
        "@prefix : <https://example.org/ritou/> .",
        "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
        "",
    ]

    classes = []
    for s, p, o in g:
        if p == RDF.type and is_class_uri(o):
            classes.append(s)
    order_keys = list(CLASS_INFO.keys())
    classes.sort(key=lambda u: order_keys.index(node_to_id(u)) if node_to_id(u) in order_keys else 999)

    if classes:
        lines.append("# =========================")
        lines.append("# クラス")
        lines.append("# =========================")
        lines.append("")
        for c in classes:
            lines.append(f"{format_turtle_term(c)} a rdf:Class .")
        lines.append("")

    subjects_map: Dict[Any, List[tuple]] = {}
    for s, p, o in g:
        if p == RDF.type and is_class_uri(o):
            continue
        subjects_map.setdefault(s, []).append((p, o))

    grouped_subjects: Dict[str, List[Any]] = {k: [] for k in CLASS_INFO.keys()}
    grouped_subjects["Other"] = []

    for s, po_list in subjects_map.items():
        assigned_cls = "Other"
        for p, o in po_list:
            if p == RDF.type:
                cid = node_to_id(o)
                if cid in grouped_subjects:
                    assigned_cls = cid
                    break
        grouped_subjects[assigned_cls].append(s)

    section_order = list(CLASS_INFO.keys()) + ["Other"]
    for cls_key in section_order:
        subj_list = grouped_subjects.get(cls_key, [])
        if not subj_list:
            continue
        subj_list.sort(key=lambda x: str(x))

        header_title = CLASS_INFO[cls_key]["label"] if cls_key in CLASS_INFO else "その他・外部リンク"
        lines.append("")
        lines.append("# =========================")
        lines.append(f"# {header_title}")
        lines.append("# =========================")
        lines.append("")

        for s in subj_list:
            po_list = subjects_map[s]
            po_list.sort(key=lambda po: (0 if po[0] == RDF.type else 1, str(po[0]), str(po[1])))

            lines.append(f"{format_turtle_term(s)}")
            for idx, (p, o) in enumerate(po_list):
                sep = "." if idx == len(po_list) - 1 else ";"
                lines.append(f"    {format_turtle_term(p)} {format_turtle_term(o)} {sep}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def save_graph(g: Graph):
    turtle_text = serialize_clean_turtle(g)
    TTL_PATH.write_text(turtle_text, encoding="utf-8")


def get_raw_turtle() -> str:
    ensure_dirs()
    return TTL_PATH.read_text(encoding="utf-8")


def save_raw_turtle(raw_text: str) -> Tuple[bool, str]:
    g = Graph()
    try:
        g.parse(data=raw_text, format="turtle")
    except Exception as e:
        return False, f"Turtle構文エラー: {str(e)}"
    try:
        for subject in set(g.subjects()):
            if not str(subject).startswith(BASE_URI):
                continue
            if any(is_class_uri(t) for t in g.objects(subject, RDF.type)):
                continue
            validate_entity(g, subject)
    except ValueError as e:
        return False, f"スキーマ検証エラー: {e}"
    TTL_PATH.write_text(raw_text, encoding="utf-8")
    return True, "保存しました"


def get_wiki_content(entity_id: str) -> str:
    ensure_dirs()
    fname = f"{safe_filename(entity_id)}.md"
    wpath = WIKI_DIR / fname
    if wpath.exists():
        return wpath.read_text(encoding="utf-8")
    return ""


def save_wiki_content(entity_id: str, content: str):
    ensure_dirs()
    clean_id = entity_id.strip().lstrip(":")
    fname = f"{safe_filename(clean_id)}.md"
    wpath = WIKI_DIR / fname
    if content.strip():
        wpath.write_text(content, encoding="utf-8")
    elif wpath.exists():
        wpath.unlink()


def get_all_data() -> Dict[str, Any]:
    """
    一般利用者向けUIとLLM探索の双方が使いやすい構造化データを返す。
    """
    g = load_graph()

    classes = [{"id": cid, "term": f":{cid}", **info}
               for cid, info in CLASS_INFO.items()]

    entities_map: Dict[str, Dict[str, Any]] = {}
    triples_list = []

    def ensure_entity(node):
        if isinstance(node, Literal) or is_class_uri(node) or node == RDF.type:
            return None
        nid = node_to_id(node)
        if nid not in entities_map:
            is_ext = str(node).startswith("http") and not str(node).startswith(BASE_URI)
            entities_map[nid] = {
                "id": nid,
                "term": format_turtle_term(node),
                "uri": str(node),
                "is_external": is_ext,
                "types": [],
                "primary_type": "Other",
                "type_info": {"label": "項目", "short": "項目", "icon": "", "color": "slate"},
                "outgoing": [],
                "incoming": [],
                "literals": {},
                "wiki_content": "" if is_ext else get_wiki_content(nid),
            }
        return entities_map[nid]

    for s, p, o in g:
        if p == RDF.type and is_class_uri(o):
            continue

        subj_ent = ensure_entity(s)
        p_id = "a" if p == RDF.type else node_to_id(p)

        if p == RDF.type:
            type_id = node_to_id(o)
            if subj_ent and type_id not in subj_ent["types"]:
                subj_ent["types"].append(type_id)
        else:
            if isinstance(o, Literal):
                if subj_ent:
                    subj_ent["literals"].setdefault(p_id, []).append(str(o))
            else:
                obj_ent = ensure_entity(o)
                if subj_ent and obj_ent:
                    subj_ent["outgoing"].append({
                        "predicate": p_id,
                        "predicate_label": PREDICATE_NATURAL_LABELS.get(p_id, {}).get("outgoing", p_id),
                        "object_id": obj_ent["id"],
                        "object_term": obj_ent["term"],
                        "object_is_external": obj_ent["is_external"],
                        "object_uri": obj_ent["uri"],
                    })
                    obj_ent["incoming"].append({
                        "subject_id": subj_ent["id"],
                        "subject_term": subj_ent["term"],
                        "predicate": p_id,
                        "predicate_label": PREDICATE_NATURAL_LABELS.get(p_id, {}).get("incoming", f"{p_id}（逆参照）"),
                    })

        triples_list.append({
            "subject": format_turtle_term(s),
            "subject_id": node_to_id(s),
            "predicate": "a" if p == RDF.type else format_turtle_term(p),
            "predicate_raw": p_id,
            "object": format_turtle_term(o),
            "object_id": node_to_id(o) if not isinstance(o, Literal) else str(o),
            "is_literal": isinstance(o, Literal),
        })

    # 各エンティティの primary_type と表示ラベルを設定
    for ent in entities_map.values():
        if ent["types"]:
            pt = ent["types"][0]
            ent["primary_type"] = pt
            ent["type_info"] = CLASS_INFO.get(pt, {"label": pt, "short": pt, "icon": "", "color": "slate"})
        elif ent["is_external"]:
            ent["primary_type"] = "ExternalLink"
            ent["type_info"] = CLASS_INFO["ExternalLink"]

    from articles import associations, revision, editing_enabled

    # 記事一覧（Article クラス + data/articles/*.md）を整形
    articles_list = []
    for ent in entities_map.values():
        if "Article" in ent["types"]:
            aid = ent["id"]
            title = (ent["literals"].get("タイトル") or [aid])[0]
            atype = (ent["literals"].get("種別") or ["研究・実践記事"])[0]
            adate = (ent["literals"].get("日付") or [""])[0]
            fpath = (ent["literals"].get("ファイルパス") or [f"{aid}.md"])[0]
            md_path = ARTICLES_DIR / fpath
            content = md_path.read_text(encoding="utf-8") if md_path.exists() else ""

            authors = [o["object_id"] for o in ent["outgoing"] if o["predicate"] == "作成者"]
            linked = associations(g, URIRef(BASE_URI + aid))
            related_research = [node_to_id(n) for n in linked['research']]
            additional_related = [node_to_id(n) for n in linked['additional']]
            related = related_research + additional_related
            ext_links = [o["object_uri"] for o in ent["outgoing"] if o["predicate"] == "外部リンク"]

            articles_list.append({
                "id": aid,
                "title": title,
                "article_type": atype,
                "date": adate,
                "filename": fpath,
                "content": content,
                "authors": authors,
                "related_entities": related,
                "related_research": related_research,
                "record_subjects": [node_to_id(n) for n in g.objects(URIRef(BASE_URI + aid), RITOU["記録先"])],
                "additional_related": additional_related,
                "covered_related": [node_to_id(n) for n in linked['covered']],
                "inherited_entities": [node_to_id(n) for n in linked['inherited']],
                "thumbnail_url": (ent['literals'].get('サムネイル') or [''])[0],
                "thumbnail_alt": (ent['literals'].get('画像の説明') or [''])[0],
                "modified_at": (ent['literals'].get('更新日時') or [''])[0],
                "revision": revision(g, URIRef(BASE_URI + aid), content),
                "external_links": ext_links,
            })

    articles_list.sort(key=lambda a: a.get("date", ""), reverse=True)

    # ユーザ一覧
    users_list = [e for e in entities_map.values() if "User" in e["types"]]

    return {
        "article_editing_enabled": editing_enabled(),
        "schema": SCHEMA,
        "classes": classes,
        "entities": list(entities_map.values()),
        "users": users_list,
        "articles": articles_list,
        "triples": triples_list,
    }


def upsert_entity_with_relations(
    entity_id: str,
    entity_class: str,
    summary: Optional[str] = None,
    wiki_content: Optional[str] = None,
    relations: List[Dict[str, str]] = None,
    external_links: List[str] = None,
    replace_relations: bool = False,
    attributes: Optional[Dict[str, str]] = None,
    replace_predicates: Optional[List[str]] = None,
):
    """
    専門知識不要のフォームから基本情報（地域・課題・研究・技術・ユーザなど）を登録・更新する。
    """
    clean_id = entity_id.strip().lstrip(":")
    if not clean_id:
        return
    g = load_graph()
    s_node = URIRef(BASE_URI + clean_id)

    # フォームが編集する関係だけを置換し、出典・役割・文字属性を保持する。
    predicates = replace_predicates
    if predicates is None and replace_relations:
        predicates = [r["predicate"] for r in (relations or [])]
    for predicate in predicates or []:
        g.remove((s_node, str_to_node(predicate), None))

    if entity_class:
        g.add((s_node, RDF.type, URIRef(BASE_URI + entity_class.strip().lstrip(":"))))

    if summary is not None:
        g.remove((s_node, URIRef(BASE_URI + "概要"), None))
        if summary.strip():
            g.add((s_node, URIRef(BASE_URI + "概要"), Literal(summary.strip())))

    if relations:
        for rel in relations:
            pred = rel.get("predicate", "").strip().lstrip(":")
            obj = rel.get("object", "").strip()
            if not pred or not obj:
                continue
            p_node = str_to_node(pred, is_object=False)
            o_node = str_to_node(obj, is_object=True, predicate_name=pred)
            g.add((s_node, p_node, o_node))

            # もし目的語にクラスがまだ無ければ、述語から推測されるクラスを自動付与して孤立を防ぐ
            if isinstance(o_node, URIRef) and str(o_node).startswith(BASE_URI):
                has_type = any(True for _ in g.triples((o_node, RDF.type, None)))
                if not has_type:
                    inferred_class = {
                        "抱える": "Issue",
                        "解決を試みる": "Issue",
                        "対象とする": "Target",
                        "活用する": "Resource",
                        "用いる": "Technology",
                        "所属する": "Laboratory",
                        "である": "Status",
                    }.get(pred)
                    if inferred_class:
                        g.add((o_node, RDF.type, URIRef(BASE_URI + inferred_class)))

    if external_links is not None:
        g.remove((s_node, URIRef(BASE_URI + "外部リンク"), None))
        for link in external_links:
            l_clean = link.strip()
            if l_clean:
                g.add((s_node, URIRef(BASE_URI + "外部リンク"), str_to_node(l_clean, is_object=True)))

    for key, value in (attributes or {}).items():
        if key not in SCHEMA["attributes"]:
            raise ValueError(f"未定義の文字属性です: {key}")
        g.remove((s_node, RITOU[key], None))
        if value.strip():
            g.add((s_node, RITOU[key], Literal(value.strip())))
    validate_entity(g, s_node)
    # 記述・参加記録を入口の項目からたどれるよう、逆向きの索引を同期する。
    if (s_node, RDF.type, RITOU.Evidence) in g:
        g.remove((None, RITOU['根拠記述'], s_node))
        for target in g.objects(s_node, RITOU['記述対象']):
            g.add((target, RITOU['根拠記述'], s_node))
    if (s_node, RDF.type, RITOU.Participation) in g:
        g.remove((None, RITOU['参加関係'], s_node))
        for activity in g.objects(s_node, RITOU['活動']):
            g.add((activity, RITOU['参加関係'], s_node))
    save_graph(g)
    if wiki_content is not None:
        save_wiki_content(clean_id, wiki_content)
    return clean_id


def validate_entity(g: Graph, node):
    """更新対象を検証する。既存の従来データに一括移行を強制しない。"""
    types = {node_to_id(t) for t in g.objects(node, RDF.type)}
    if not types or not types <= CLASS_INFO.keys():
        raise ValueError("スキーマに定義された種類を指定してください")
    for p, o in g.predicate_objects(node):
        name = node_to_id(p)
        if p == RDF.type:
            continue
        if isinstance(o, Literal):
            if name in SCHEMA['relations']:
                raise ValueError(f"{name}には項目への参照が必要です")
            spec = SCHEMA["attributes"].get(name)
            if spec and spec.get("enum") and str(o) not in spec["enum"]:
                raise ValueError(f"{name}の値が不正です")
            if "Result" in types and name == "種別" and str(o) not in {"実施実績", "評価結果"}:
                raise ValueError("成果の種別は実施実績または評価結果を指定してください")
        else:
            if name in SCHEMA['attributes']:
                raise ValueError(f"{name}は文字属性です")
            spec = SCHEMA["relations"].get(name)
            if not spec or spec.get("legacy"):
                continue
            if "*" not in spec["domains"] and not types.intersection(spec["domains"]):
                raise ValueError(f"{name}はこの種類から使えません")
            target_types = {node_to_id(t) for t in g.objects(o, RDF.type)}
            if name == "外部リンク" and str(o).startswith(("http://", "https://")) and not str(o).startswith(BASE_URI):
                continue
            if "*" not in spec["ranges"] and not target_types.intersection(spec["ranges"]):
                raise ValueError(f"{name}の関連先は {' / '.join(spec['ranges'])} を指定してください")
    for cls in types:
        spec = SCHEMA["required"].get(cls, {})
        for name in spec.get("relations", []):
            if not any(isinstance(o, URIRef) for o in g.objects(node, RITOU[name])):
                raise ValueError(f"{cls}には関係「{name}」が必要です")
        for name in spec.get("attributes", []):
            if not any(isinstance(o, Literal) and str(o).strip() for o in g.objects(node, RITOU[name])):
                raise ValueError(f"{cls}には文字属性「{name}」が必要です")
        if spec.get("one_of"):
            count = sum(bool(list(g.objects(node, RITOU[name]))) for name in spec["one_of"])
            if count != 1:
                raise ValueError("出典付き記述には値または関係先の一方だけを指定してください")
    if 'Evidence' in types:
        for name in ['記述対象', '対象述語']:
            if len(list(g.objects(node, RITOU[name]))) != 1:
                raise ValueError(f'出典付き記述の{name}は一つにしてください')
        predicate = str(g.value(node, RITOU['対象述語']))
        if predicate not in SCHEMA['relations'] and predicate not in SCHEMA['attributes']:
            raise ValueError('対象述語はスキーマで定義された関係名または文字属性名を指定してください')
        object_predicate = '関係先' if predicate in SCHEMA['relations'] else '値'
        if not list(g.objects(node, RITOU[object_predicate])):
            raise ValueError(f'対象述語「{predicate}」には{object_predicate}を指定してください')
        if len(list(g.objects(node, RITOU[object_predicate]))) != 1:
            raise ValueError('一つの出典付き記述には一つの値・関係先を指定してください')
    if 'Participation' in types:
        for name in ['主体', '活動']:
            if len(list(g.objects(node, RITOU[name]))) != 1:
                raise ValueError(f'参加記録の{name}は一つにしてください')


def delete_entity(entity_id: str):
    clean_id = entity_id.strip().lstrip(":")
    g = load_graph()
    node = URIRef(BASE_URI + clean_id)
    affected = set(g.subjects(None, node)) - {node}
    g.remove((node, None, None))
    g.remove((None, None, node))
    for subject in affected:
        if any(True for _ in g.triples((subject, RDF.type, None))):
            validate_entity(g, subject)
    save_graph(g)
    save_wiki_content(clean_id, "")


def save_article(article_id: str, title: str, article_type: str, content: str,
                 author=None, related_entities=None, external_links=None,
                 related_research=None, thumbnail_url=None, thumbnail_alt=None,
                 expected_revision=None, entry_point=None, context_id=None) -> str:
    from articles import save_article as save
    return save(article_id=article_id, title=title, article_type=article_type,
                content=content, author=author, related_entities=related_entities,
                external_links=external_links, related_research=related_research,
                thumbnail_url=thumbnail_url, thumbnail_alt=thumbnail_alt,
                expected_revision=expected_revision, entry_point=entry_point, context_id=context_id)
