"""地域の投稿と既存のIssueを一つの知識基盤で扱う。"""
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
import math
from threading import RLock
from uuid import uuid4
from typing import Literal as Choice

from pydantic import BaseModel, ConfigDict, Field, model_validator
from rdflib import Literal, RDF
import rdf_manager as db

STATES = ['未整理', '相談受付', '調査・検討中', '取組中', '解決・一区切り']
FIELDS = {'title':'タイトル', 'description':'説明', 'background':'背景条件',
          'desired_support':'希望する支援', 'status':'課題状況', 'category':'課題分野',
          'reporter_name':'投稿者名', 'reporter_role':'投稿者の立場'}
_lock = RLock()


class ConflictError(ValueError):
    pass


class IssueInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue_id: str = Field(default='', max_length=500)
    expected_revision: str = ''
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default='', max_length=12000)
    background: str = Field(default='', max_length=6000)
    desired_support: str = Field(default='', max_length=3000)
    status: Choice['未整理','相談受付','調査・検討中','取組中','解決・一区切り'] = '相談受付'
    category: Choice['','暮らし・福祉','交通・物流','防災','環境・エネルギー','産業・観光','教育・文化','情報・通信','その他'] = ''
    reporter_name: str = Field(default='', max_length=200)
    reporter_role: Choice['','住民','行政','学生','教員','地域団体・事業者','その他'] = ''
    target_ids: list[str] = Field(default_factory=list, max_length=30)
    new_target: str = Field(default='', max_length=200)
    research_ids: list[str] = Field(default_factory=list, max_length=50)
    new_target_latitude: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    new_target_longitude: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    new_target_precision: Choice['おおよその場所','施設・現場の位置'] = 'おおよその場所'

    @model_validator(mode='after')
    def valid(self):
        if not self.title.strip():
            raise ValueError('課題のタイトルを入力してください')
        if (self.new_target_latitude is None) != (self.new_target_longitude is None):
            raise ValueError('緯度と経度は両方を指定してください')
        if self.new_target_latitude is not None and not self.new_target.strip():
            raise ValueError('位置を持つ地域・現場の名前を入力してください')
        return self


def revision(g, node):
    triples = list(g.triples((node, None, None)))
    triples += list(g.triples((None, db.RITOU['抱える'], node)))
    triples += list(g.triples((None, db.RITOU['解決を試みる'], node)))
    return hashlib.sha256('\n'.join(sorted(' '.join(x.n3() for x in t) for t in triples)).encode()).hexdigest()


def place_record(g, node):
    def text(key):
        return str(g.value(node,db.RITOU[key]) or '')
    def coordinate(key,limit):
        try:
            value=float(text(key))
            return value if math.isfinite(value) and abs(value)<=limit else None
        except ValueError:
            return None
    return {'id':db.node_to_id(node),'title':text('タイトル') or db.node_to_id(node),
            'summary':text('概要'),'latitude':coordinate('緯度',90),'longitude':coordinate('経度',180),
            'location_precision':text('位置の精度') or 'おおよその場所',
            'revision':revision(g,node)}


def list_places():
    g=db.load_graph()
    return {'items':sorted([place_record(g,n) for n in g.subjects(RDF.type,db.RITOU.Target)],key=lambda p:p['title'])}


class PlaceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: str = Field(default='',max_length=500)
    expected_revision: str = ''
    title: str = Field(min_length=1,max_length=200)
    summary: str = Field(default='',max_length=6000)
    latitude: float | None = Field(default=None,ge=-90,le=90,allow_inf_nan=False)
    longitude: float | None = Field(default=None,ge=-180,le=180,allow_inf_nan=False)
    location_precision: Choice['おおよその場所','施設・現場の位置'] = 'おおよその場所'

    @model_validator(mode='after')
    def valid(self):
        if not self.title.strip():raise ValueError('地域・現場の名前を入力してください')
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError('緯度と経度は両方を指定してください')
        return self


def save_place(body: PlaceInput):
    with _lock:
        g=db.load_graph()
        target_id=body.target_id or 'target_'+uuid4().hex[:16]
        node=db.RITOU[target_id]
        if body.target_id:
            if (node,RDF.type,db.RITOU.Target) not in g:raise ValueError('地域・現場が見つかりません')
            if body.expected_revision!=revision(g,node):raise ConflictError('別の更新がありました。再読み込みしてから編集してください。')
        g.add((node,RDF.type,db.RITOU.Target))
        for key,value in {'タイトル':body.title.strip(),'概要':body.summary.strip(),
                          '緯度':'' if body.latitude is None else str(body.latitude),
                          '経度':'' if body.longitude is None else str(body.longitude),
                          '位置の精度':body.location_precision}.items():
            g.remove((node,db.RITOU[key],None))
            if value:g.add((node,db.RITOU[key],Literal(value)))
        db.validate_entity(g,node)
        db.save_graph(g)
        return {'ok':True,'target_id':target_id,'revision':revision(g,node)}


def list_issues():
    g = db.load_graph()
    def text(node, key):
        return str(g.value(node, db.RITOU[key]) or '')
    def label(node):
        return text(node, 'タイトル') or db.node_to_id(node)
    items=[]
    for node in g.subjects(RDF.type, db.RITOU.Issue):
        item={field:text(node, predicate) for field,predicate in FIELDS.items()}
        item.update(id=db.node_to_id(node), revision=revision(g,node),
                    title=text(node,'タイトル') or db.node_to_id(node),
                    description=text(node,'説明') or text(node,'概要'),
                    status=text(node,'課題状況') or '未整理',
                    created_at=text(node,'作成日'), updated_at=text(node,'課題更新日時'),
                    targets=[place_record(g,n) for n in sorted(g.subjects(db.RITOU['抱える'],node),key=str)],
                    research=[{'id':db.node_to_id(n),'title':label(n)} for n in g.subjects(db.RITOU['解決を試みる'],node)])
        items.append(item)
    return {'items': sorted(items,key=lambda x:(x['updated_at'] or x['created_at'],x['id']),reverse=True), 'states':STATES}


def save_issue(body: IssueInput):
    # 一つの要求の検証が済むまでディスクには書かない。
    with _lock:
        g=db.load_graph()
        issue_id=body.issue_id or 'issue_'+uuid4().hex[:16]
        node=db.RITOU[issue_id]
        if body.issue_id:
            if (node,RDF.type,db.RITOU.Issue) not in g:
                raise ValueError('編集対象の課題が見つかりません')
            if body.expected_revision != revision(g,node):
                raise ConflictError('別の更新がありました。ページを再読み込みしてから編集してください。入力内容は保存されていません。')
        for ids,cls in [(body.target_ids,'Target'),(body.research_ids,'Research')]:
            for id_ in ids:
                if (db.RITOU[id_],RDF.type,db.RITOU[cls]) not in g:
                    raise ValueError('関連先の地域・現場または研究・活動が見つかりません')
        g.add((node,RDF.type,db.RITOU.Issue))
        values={predicate:getattr(body,field).strip() for field,predicate in FIELDS.items()}
        values['概要']=body.description.strip()[:500]
        now=datetime.now(ZoneInfo('Asia/Tokyo')).isoformat(timespec='microseconds')
        values['課題更新日時']=now
        if not body.issue_id: values['作成日']=now
        for predicate,value in values.items():
            g.remove((node,db.RITOU[predicate],None))
            if value:g.add((node,db.RITOU[predicate],Literal(value)))
        targets=list(dict.fromkeys(body.target_ids))
        if body.new_target.strip():
            target_id='target_'+uuid4().hex[:16]
            target=db.RITOU[target_id]
            g.add((target,RDF.type,db.RITOU.Target))
            g.add((target,db.RITOU['タイトル'],Literal(body.new_target.strip())))
            if body.new_target_latitude is not None:
                g.add((target,db.RITOU['緯度'],Literal(str(body.new_target_latitude))))
                g.add((target,db.RITOU['経度'],Literal(str(body.new_target_longitude))))
                g.add((target,db.RITOU['位置の精度'],Literal(body.new_target_precision)))
            db.validate_entity(g,target)
            targets.append(target_id)
        for predicate,ids in [('抱える',targets),('解決を試みる',body.research_ids)]:
            g.remove((None,db.RITOU[predicate],node))
            for id_ in set(ids): g.add((db.RITOU[id_],db.RITOU[predicate],node))
        db.validate_entity(g,node)
        db.save_graph(g)
        return {'ok':True,'issue_id':issue_id,'revision':revision(g,node)}
