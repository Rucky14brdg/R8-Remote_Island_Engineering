from pathlib import Path
import json
import re
from typing import List, Dict, Optional, Literal
from fastapi import FastAPI, HTTPException, File, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import rdf_manager
import llm_agent
import portfolio
import articles
import activity_records
import issues

app = FastAPI(title="離島工学データベース（知識基盤）")

TEMPLATES_DIR = Path(__file__).parent / "templates"
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


class ParticipantInput(BaseModel):
    name: str
    role: str = "担当"
    actor_class: str = "User"


class ActivityInput(BaseModel):
    entity_id: str = ""
    title: str
    summary: str = ""
    description: str = ""
    period: str = ""
    goal: str = ""
    targets: List[str] = []
    issues: List[str] = []
    technologies: List[str] = []
    resources: List[str] = []
    participants: Optional[List[ParticipantInput]] = None
    external_links: List[str] = []


class ProfileInput(BaseModel):
    entity_id: str
    display_name: str = ""
    headline: str = ""
    summary: str = ""
    description: str = ""
    keywords: List[str] = []
    affiliations: List[str] = []
    statuses: List[str] = []
    external_links: List[str] = []


class ActivityRecordInput(BaseModel):
    activity_id: str
    record_kind: str = "progress"
    headline: str = ""
    content: str = ""
    activity_period: str = ""
    recorded_by: str = ""
    source_mode: str = "self"
    source_id: str = ""
    article_id: str = ""
    article_revision: str = ""
    article_excerpt: str = ""
    source_title: str = ""
    source_url: str = ""
    source_published: str = ""
    source_description: str = ""
    source_locator: str = ""
    confirmation: str = "未確認"


class RelationItem(BaseModel):
    predicate: str
    object: str


class EntityUpsertInput(BaseModel):
    entity_id: str
    entity_class: str
    summary: Optional[str] = None
    wiki_content: Optional[str] = None
    relations: List[RelationItem] = []
    external_links: Optional[List[str]] = None
    replace_relations: bool = False
    replace_predicates: Optional[List[str]] = None
    attributes: Dict[str, str] = {}


class WikiUpdateInput(BaseModel):
    entity_id: str
    summary: Optional[str] = None
    wiki_content: str


class ArticleSaveInput(BaseModel):
    article_id: str = ""
    title: str
    article_type: str = "ニュース・発表"
    content: str
    author: Optional[str] = None
    related_entities: Optional[List[str]] = None
    related_research: Optional[List[str]] = None
    external_links: Optional[List[str]] = None
    thumbnail_url: Optional[str] = None
    thumbnail_alt: Optional[str] = None
    expected_revision: Optional[str] = None
    entry_point: Optional[str] = None
    context_id: Optional[str] = None


class RawTurtleInput(BaseModel):
    content: str


class AiHistoryItem(BaseModel):
    role: Literal['user', 'assistant']
    content: str = Field(max_length=12000)


class ExploreInput(BaseModel):
    question: str = Field(min_length=1, max_length=3000)
    model: str = Field(default=llm_agent.DEFAULT_MODEL, min_length=1, max_length=120)
    history: List[AiHistoryItem] = Field(default_factory=list, max_length=8)
    mode: Literal['search', 'connect'] = 'search'  # 旧クライアント互換。現在は共通の回答方針。
    context_ids: List[str] = Field(default_factory=list, max_length=8)
    scope_id: str = Field(default='', max_length=500)
    scope_mode: Literal['auto', 'document', 'related'] = 'auto'


@app.get('/api/places')
async def api_places():
    return issues.list_places()


@app.post('/api/places')
def api_save_place(body: issues.PlaceInput):
    try:
        return issues.save_place(body)
    except issues.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get('/api/issues')
async def api_issues():
    return issues.list_issues()


@app.post('/api/issues')
def api_save_issue(body: issues.IssueInput):
    try:
        return issues.save_issue(body)
    except issues.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = TEMPLATES_DIR / "index.html"
    html = html_path.read_text(encoding="utf-8")
    def asset_version(match):
        path = Path(__file__).parent / 'static' / match.group(2)
        return f'{match.group(1)}?v={path.stat().st_mtime_ns}"'
    html = re.sub(r'((?:src|href)="/static/([^"?]+))(?:\?[^"\s]*)?"', asset_version, html)
    return HTMLResponse(content=html, headers={'Cache-Control':'no-cache'})


@app.get("/advanced", response_class=HTMLResponse)
async def advanced():
    return HTMLResponse((TEMPLATES_DIR / "legacy.html").read_text(encoding="utf-8"))


@app.post("/api/portfolio/activity")
async def save_portfolio_activity(body: ActivityInput):
    try:
        return {"ok": True, "entity_id": portfolio.save_activity(body)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/portfolio/profile")
async def save_portfolio_profile(body: ProfileInput):
    if not body.entity_id.strip():
        raise HTTPException(status_code=400, detail="名前を入力してください")
    try:
        return {"ok": True, "entity_id": portfolio.save_profile(body)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/portfolio/activity-record")
async def api_save_activity_record(body: ActivityRecordInput):
    try:
        return {"ok": True, **activity_records.save_record(body)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/data")
async def api_get_data():
    data = rdf_manager.get_all_data()
    data["raw_turtle"] = rdf_manager.get_raw_turtle()
    return data


@app.post("/api/entity")
async def api_upsert_entity(body: EntityUpsertInput):
    if not body.entity_id.strip():
        raise HTTPException(status_code=400, detail="名前を入力してください")
    try:
        saved_id = rdf_manager.upsert_entity_with_relations(
            entity_id=body.entity_id,
            entity_class=body.entity_class,
            summary=body.summary,
            wiki_content=body.wiki_content,
            relations=[{"predicate": r.predicate, "object": r.object} for r in body.relations],
            external_links=body.external_links,
            replace_relations=body.replace_relations,
            replace_predicates=body.replace_predicates,
            attributes=body.attributes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "entity_id": saved_id}


@app.get("/api/schema")
async def api_schema():
    return rdf_manager.SCHEMA


@app.post("/api/wiki")
async def api_update_wiki(body: WikiUpdateInput):
    if body.summary is not None:
        g = rdf_manager.load_graph()
        s_node = rdf_manager.URIRef(rdf_manager.BASE_URI + body.entity_id.strip().lstrip(":"))
        g.remove((s_node, rdf_manager.URIRef(rdf_manager.BASE_URI + "概要"), None))
        if body.summary.strip():
            g.add((s_node, rdf_manager.URIRef(rdf_manager.BASE_URI + "概要"), rdf_manager.Literal(body.summary.strip())))
        rdf_manager.save_graph(g)
    rdf_manager.save_wiki_content(body.entity_id, body.wiki_content)
    return {"ok": True}


@app.delete("/api/entity/{entity_id}")
async def api_delete_entity(entity_id: str):
    try:
        rdf_manager.delete_entity(entity_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"参照中の記録を先に修正してください: {exc}") from exc
    return {"ok": True}


@app.post("/api/article")
async def api_save_article(body: ArticleSaveInput):
    try:
        saved_id = rdf_manager.save_article(
            article_id=body.article_id,
            title=body.title,
            article_type=body.article_type,
            content=body.content,
            author=body.author,
            related_entities=body.related_entities,
            external_links=body.external_links,
            related_research=body.related_research,
            thumbnail_url=body.thumbnail_url,
            thumbnail_alt=body.thumbnail_alt,
            expected_revision=body.expected_revision,
            entry_point=body.entry_point,
            context_id=body.context_id,
        )
    except articles.ArticleConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "article_id": saved_id}


@app.post("/api/article/thumbnail")
async def api_article_thumbnail(file: UploadFile = File(...)):
    try:
        content = await file.read(articles.MAX_UPLOAD_BYTES + 1)
        return {"ok": True, "url": articles.store_thumbnail(content)}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()


@app.get("/media/article-thumbnails/{filename}")
async def api_article_thumbnail_file(filename: str):
    path = articles._local_thumbnail('/media/article-thumbnails/' + filename)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="画像が見つかりません")
    return FileResponse(path, media_type="image/webp", headers={"X-Content-Type-Options": "nosniff"})


@app.delete("/api/article/{article_id}")
async def api_delete_article(article_id: str):
    try:
        rdf_manager.delete_entity(article_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    md_path = rdf_manager.ARTICLES_DIR / f"{article_id}.md"
    if md_path.exists():
        md_path.unlink()
    return {"ok": True}


@app.get("/api/turtle", response_class=PlainTextResponse)
async def api_get_turtle():
    return rdf_manager.get_raw_turtle()


@app.post("/api/turtle")
async def api_save_turtle(body: RawTurtleInput):
    ok, msg = rdf_manager.save_raw_turtle(body.content)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": msg}


@app.get("/api/ollama/status")
async def api_ollama_status(model: str = llm_agent.DEFAULT_MODEL):
    return await llm_agent.check_ollama_status(model)


@app.post("/api/explore")
async def api_explore(body: ExploreInput):
    if not body.question.strip():
        raise HTTPException(status_code=400, detail='質問を入力してください')
    return await llm_agent.explore_knowledge_base(
        question=body.question,
        model=body.model,
        history=[m.model_dump() for m in body.history], mode=body.mode, context_ids=body.context_ids,
        scope_id=body.scope_id, scope_mode=body.scope_mode,
    )


@app.post('/api/ai/chat')
async def api_ai_chat(body: ExploreInput):
    if not body.question.strip():
        raise HTTPException(status_code=400, detail='質問を入力してください')
    async def stream():
        async for event in llm_agent.explore_events(
                body.question, model=body.model, history=[m.model_dump() for m in body.history],
                mode=body.mode, context_ids=body.context_ids, scope_id=body.scope_id, scope_mode=body.scope_mode):
            yield json.dumps(event, ensure_ascii=False) + '\n'
    return StreamingResponse(stream(), media_type='application/x-ndjson',
                             headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
