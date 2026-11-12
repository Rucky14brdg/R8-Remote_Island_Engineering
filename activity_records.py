"""研究・活動の進捗、実績、資料を用途別フォームから記録する。"""
from datetime import datetime
import hashlib
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo
from rdflib import Literal, RDF, URIRef
import rdf_manager as db


KINDS = {'progress': '実施状況', 'implementation': '実施実績', 'evaluation': '評価結果', 'source': '根拠資料'}
CONFIRMATIONS = {'未確認', '原資料確認済み', '二次資料で確認', '追加確認が必要'}


def save_record(body):
    g = db.load_graph()
    activity = db.RITOU[body.activity_id]
    if (activity, RDF.type, db.RITOU.Research) not in g:
        raise ValueError('記録先の研究・活動が見つかりません')
    if body.record_kind not in KINDS:
        raise ValueError('記録の種類を選択してください')
    if body.source_mode not in {'self', 'new', 'existing', 'article'}:
        raise ValueError('記録の根拠を選択してください')
    is_source = body.record_kind == 'source'
    content = body.content.strip()
    if body.source_mode != 'article' and not is_source and not content:
        raise ValueError('進捗・実績の内容を入力してください')
    if is_source and body.source_mode == 'self':
        raise ValueError('追加する資料を選択するか、資料名を入力してください')
    author = db.RITOU[body.recorded_by] if body.recorded_by else None
    if author and (author, RDF.type, db.RITOU.User) not in g:
        raise ValueError('記録者は登録済みの研究者から選択してください')
    if body.source_mode == 'self' and author is None:
        raise ValueError('入力内容を記録する研究者を選択してください')
    now = datetime.now(ZoneInfo('Asia/Tokyo')).isoformat(timespec='seconds')
    touched = {activity}

    article = None
    article_content = ''
    if body.source_mode == 'article':
        from articles import associations, revision
        article = db.RITOU[body.article_id]
        if ((article, RDF.type, db.RITOU.Article) not in g
                or str(g.value(article, db.RITOU['種別'])) != '研究・実践記事'
                or activity not in associations(g, article)['research']):
            raise ValueError('この研究に紐付く研究・実践記事を選択してください')
        if body.record_kind not in {'implementation', 'evaluation'}:
            raise ValueError('記事から整理する種類は実施実績または評価結果を選択してください')
        filename = str(g.value(article, db.RITOU['ファイルパス']) or (body.article_id + '.md'))
        if Path(filename).name != filename:
            raise ValueError('記事のファイルパスが不正です')
        path = db.ARTICLES_DIR / filename
        article_content = path.read_text(encoding='utf-8') if path.exists() else ''
        if not body.article_revision or body.article_revision != revision(g, article, article_content):
            raise ValueError('記事が更新されています。記事を開き直してから整理してください')
        content = body.article_excerpt.strip()
        if not content or content not in article_content:
            raise ValueError('元の記事の本文から、整理する箇所を選択してください')
        # 同じ箇所・種類・時期を再送した場合も、一件の整理として扱う。
        key = '\n'.join([body.activity_id, body.article_id, body.article_revision,
                         content, body.record_kind, body.activity_period.strip()])
        evidence_id = 'evidence_article_' + hashlib.sha256(key.encode()).hexdigest()[:24]
        existing = db.RITOU[evidence_id]
        if (existing, RDF.type, db.RITOU.Evidence) in g:
            return {'record_id': evidence_id,
                    'result_id': db.node_to_id(g.value(existing, db.RITOU['記述対象'])),
                    'source_id': db.node_to_id(g.value(existing, db.RITOU['出典']))}

    def text(node, key, value):
        if value and value.strip():
            g.add((node, db.RITOU[key], Literal(value.strip())))

    if body.source_mode == 'article':
        source_key = hashlib.sha256((body.article_id + body.article_revision).encode()).hexdigest()[:24]
        source = db.RITOU['source_article_' + source_key]
        if (source, RDF.type, db.RITOU.Source) not in g:
            g.add((source, RDF.type, db.RITOU.Source))
            g.add((source, db.RITOU['原記事'], article))
            text(source, 'タイトル', str(g.value(article, db.RITOU['タイトル']) or body.article_id))
            text(source, '資料区分', '記事記録')
            text(source, '記事版', body.article_revision)
            text(source, '発行時点', str(g.value(article, db.RITOU['日付']) or ''))
            g.add((source, db.RITOU['説明'], Literal(article_content)))
            text(source, '記録時点', now)
            for creator in g.objects(article, db.RITOU['作成者']):
                g.add((source, db.RITOU['作成者'], creator))
            if author:
                g.add((source, db.RITOU['記録者'], author))
        touched.add(source)
    elif body.source_mode == 'existing':
        source = db.RITOU[body.source_id]
        if (source, RDF.type, db.RITOU.Source) not in g:
            raise ValueError('登録済みの資料を選択してください')
    else:
        source = db.RITOU['source_record_' + uuid4().hex[:16]]
        g.add((source, RDF.type, db.RITOU.Source))
        if body.source_mode == 'self':
            display_name = str(g.value(author, db.RITOU['表示名']) or db.node_to_id(author))
            text(source, 'タイトル', display_name + 'による入力記録')
            text(source, '資料区分', '入力記録')
            text(source, '発行時点', now[:10])
            text(source, '説明', body.content)
            g.add((source, db.RITOU['作成者'], author))
        else:
            if not body.source_title.strip():
                raise ValueError('資料名を入力してください')
            text(source, 'タイトル', body.source_title)
            text(source, '資料区分', '参考資料')
            text(source, '発行時点', body.source_published)
            text(source, '説明', body.source_description)
            url = body.source_url.strip()
            if url:
                parsed = urlsplit(url)
                if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
                    raise ValueError('資料のURLはhttp://またはhttps://で入力してください')
                g.add((source, db.RITOU['外部リンク'], URIRef(url)))
        text(source, '記録時点', now)
        if author:
            g.add((source, db.RITOU['記録者'], author))
        touched.add(source)
    if body.source_mode not in {'self', 'article'}:
        g.add((activity, db.RITOU['参照資料'], source))
    if is_source:
        # 資料を登録しただけでは、その内容の検証や成果を主張しない。
        for node in touched:
            db.validate_entity(g, node)
        db.save_graph(g)
        return {'source_id': db.node_to_id(source), 'record_id': None}

    location = ('本文の引用箇所：\n' + content if article else
                'この画面への入力内容' if body.source_mode == 'self' else body.source_locator.strip())
    if not location:
        raise ValueError('資料の該当箇所を入力してください（ページ・節・図表名、または「資料全体」）')
    input_source = str(g.value(source, db.RITOU['資料区分'])) in {'入力記録', '記事記録'}
    confirmation = '未確認' if input_source or body.source_mode in {'self', 'article'} else body.confirmation
    if confirmation not in CONFIRMATIONS:
        raise ValueError('資料の確認状況を選択してください')
    headline = body.headline.strip() or content.splitlines()[0][:60]
    evidence = db.RITOU[evidence_id if article else 'evidence_' + uuid4().hex[:16]]
    g.add((evidence, RDF.type, db.RITOU.Evidence))
    target = activity
    result = None
    if body.record_kind != 'progress':
        result = db.RITOU['result_' + uuid4().hex[:16]]
        g.add((result, RDF.type, db.RITOU.Result))
        text(result, 'タイトル', headline)
        text(result, '種別', KINDS[body.record_kind])
        text(result, '概要', content)
        text(result, '説明', content)
        text(result, '活動時期', body.activity_period)
        text(result, '記録時点', now)
        if author:
            g.add((result, db.RITOU['記録者'], author))
        g.add((activity, db.RITOU['実績を持つ'], result))
        target = result
        touched.add(result)
    g.add((evidence, db.RITOU['記述対象'], target))
    g.add((evidence, db.RITOU['出典'], source))
    g.add((target, db.RITOU['根拠記述'], evidence))
    text(evidence, 'タイトル', headline)
    text(evidence, '対象述語', '実施状況' if body.record_kind == 'progress' else '説明')
    text(evidence, '値', content)
    text(evidence, '記述内容', content)
    text(evidence, '記述区分', KINDS[body.record_kind])
    text(evidence, '確認状況', confirmation)
    text(evidence, '該当箇所', location)
    text(evidence, '活動時期', body.activity_period)
    text(evidence, '記録時点', now)
    if author:
        g.add((evidence, db.RITOU['記録者'], author))
    touched.add(evidence)
    for node in touched:
        db.validate_entity(g, node)
    db.save_graph(g)
    return {'record_id': db.node_to_id(evidence), 'result_id': db.node_to_id(result) if result else None, 'source_id': db.node_to_id(source)}
