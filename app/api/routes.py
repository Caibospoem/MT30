"""REST 接口层（M1 子集，路径与技术方案 4.2 一致）。

统一响应：{code: 0, message: "ok", data: ...}；错误码见方案 4.4。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..collector.git_collector import CollectorError
from ..collector.sync import SyncError, sync_repository
from ..config import get_settings
from ..db import get_session
from ..knowledge import service as ksvc
from ..models.tables import Commit, KnowledgeEntry, Repository, RetrievalLog
from ..retrieval.searcher import search
from ..vectorstore import get_vector_store

router = APIRouter(prefix="/api/v1")


# ─────────────────── 请求模型 ───────────────────
class RepoCreateReq(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    git_url: str = Field(min_length=1, max_length=512)
    default_branch: str = "main"
    language: str = "java"


class SyncReq(BaseModel):
    mode: str = "incremental"  # incremental | backfill（M1 语义一致，均幂等去重）
    since: str | None = None   # ISO 日期，如 2024-01-01
    until: str | None = None
    branch: str | None = None
    limit: int | None = None


class KnowledgeEditReq(BaseModel):
    summary: str | None = None
    background: str | None = None
    core_changes: list | None = None
    modules: list | None = None
    impact: str | None = None
    tags: list[str] | None = None
    risk_note: str | None = None
    reusable_note: str | None = None
    comment: str = ""


class ReviewReq(BaseModel):
    reviewed_by: str = Field(min_length=1)
    comment: str = ""


class SearchReq(BaseModel):
    repo_id: int
    query: str = Field(min_length=1)
    top_k: int | None = None
    tags: list[str] | None = None
    created_by: str = ""


# ─────────────────── 异常 → HTTP ───────────────────
def _err(status: int, code: int, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


@router.get("/health")
def health():
    return {"code": 0, "message": "ok", "data": {"status": "up", "llm_provider": get_settings().llm_provider}}


# ─────────────────── 仓库管理 ───────────────────
@router.post("/repositories")
def create_repository(req: RepoCreateReq, db: Session = Depends(get_session)):
    repo = Repository(name=req.name, git_url=req.git_url,
                      default_branch=req.default_branch, language=req.language)
    db.add(repo)
    db.flush()
    return {"code": 0, "message": "ok", "data": {"id": repo.id, "name": repo.name}}


@router.get("/repositories")
def list_repositories(db: Session = Depends(get_session)):
    repos = db.query(Repository).order_by(Repository.id).all()
    return {"code": 0, "message": "ok", "data": [
        {"id": r.id, "name": r.name, "git_url": r.git_url,
         "default_branch": r.default_branch, "language": r.language} for r in repos
    ]}


@router.post("/repositories/{repo_id}/sync")
def sync_repo(repo_id: int, req: SyncReq, db: Session = Depends(get_session)):
    repo = db.get(Repository, repo_id)
    if not repo:
        raise _err(404, 40401, f"仓库 {repo_id} 不存在")
    try:
        stats = sync_repository(db, repo, req.mode, req.since, req.until, req.branch, req.limit)
    except SyncError as exc:
        raise _err(400, 40002, str(exc)) from exc
    except CollectorError as exc:
        raise _err(400, 40002, f"采集失败: {exc}") from exc
    return {"code": 0, "message": "ok", "data": stats}


# ─────────────────── 知识条目 / 审核流 ───────────────────
@router.get("/knowledge")
def list_knowledge(repo_id: int | None = None, status: str | None = None, tag: str | None = None,
                   q: str | None = None, page: int = 1, page_size: int = 10,
                   db: Session = Depends(get_session)):
    query = db.query(KnowledgeEntry)
    if repo_id is not None:
        query = query.filter(KnowledgeEntry.repo_id == repo_id)
    if status:
        query = query.filter(KnowledgeEntry.status == status)
    if tag:
        query = query.filter(KnowledgeEntry.tags.contains(tag))
    if q:
        like = f"%{q}%"
        query = query.filter(or_(KnowledgeEntry.summary.like(like), KnowledgeEntry.background.like(like)))
    total = query.count()
    rows = (query.order_by(KnowledgeEntry.id.desc())
            .offset((max(page, 1) - 1) * page_size).limit(page_size).all())
    return {"code": 0, "message": "ok", "data": {
        "total": total, "page": page, "page_size": page_size,
        "items": [ksvc.entry_to_dict(e) for e in rows],
    }}


@router.get("/knowledge/{entry_id}")
def knowledge_detail(entry_id: int, db: Session = Depends(get_session)):
    entry = db.get(KnowledgeEntry, entry_id)
    if not entry:
        raise _err(404, 40401, f"知识条目 {entry_id} 不存在")
    return {"code": 0, "message": "ok", "data": ksvc.entry_to_dict(entry, with_files=True, session=db)}


@router.patch("/knowledge/{entry_id}")
def edit_knowledge(entry_id: int, req: KnowledgeEditReq, db: Session = Depends(get_session)):
    try:
        entry = ksvc.edit_entry(db, entry_id, req.model_dump(exclude={"comment"}), req.comment)
    except ksvc.NotFoundError as exc:
        raise _err(404, 40401, str(exc)) from exc
    return {"code": 0, "message": "ok", "data": ksvc.entry_to_dict(entry)}


@router.post("/knowledge/{entry_id}/confirm")
def confirm_knowledge(entry_id: int, req: ReviewReq, db: Session = Depends(get_session)):
    try:
        entry = ksvc.confirm_entry(db, entry_id, req.reviewed_by, req.comment)
    except ksvc.NotFoundError as exc:
        raise _err(404, 40401, str(exc)) from exc
    except ksvc.ConflictError as exc:
        raise _err(409, 40901, str(exc)) from exc
    return {"code": 0, "message": "ok", "data": {
        "id": entry.id, "status": entry.status, "version": entry.version,
        "vector_indexed": True,
    }}


@router.post("/knowledge/{entry_id}/reject")
def reject_knowledge(entry_id: int, req: ReviewReq, db: Session = Depends(get_session)):
    try:
        entry = ksvc.reject_entry(db, entry_id, req.reviewed_by, req.comment)
    except ksvc.NotFoundError as exc:
        raise _err(404, 40401, str(exc)) from exc
    return {"code": 0, "message": "ok", "data": {
        "id": entry.id, "status": entry.status,
    }}


# ─────────────────── 检索 ───────────────────
@router.post("/search")
def search_knowledge(req: SearchReq, db: Session = Depends(get_session)):
    try:
        result = search(db, req.repo_id, req.query, req.top_k, req.tags, req.created_by)
    except ValueError as exc:
        raise _err(400, 40001, str(exc)) from exc
    return {"code": 0, "message": "ok", "data": result}


# ─────────────────── 统计 ───────────────────
@router.get("/stats")
def stats(db: Session = Depends(get_session)):
    def count(model, *filters):
        q = db.query(model)
        for f in filters:
            q = q.filter(f)
        return q.count()

    return {"code": 0, "message": "ok", "data": {
        "repositories": count(Repository),
        "commits": count(Commit),
        "entries_total": count(KnowledgeEntry),
        "entries_pending": count(KnowledgeEntry, KnowledgeEntry.status == "pending_review"),
        "entries_confirmed": count(KnowledgeEntry, KnowledgeEntry.status == "confirmed"),
        "entries_rejected": count(KnowledgeEntry, KnowledgeEntry.status == "rejected"),
        "searches": count(RetrievalLog),
        "vectors": get_vector_store().count(),
    }}
