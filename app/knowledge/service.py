"""知识条目服务：状态机 + 审核流 + 向量索引（对应技术方案 3.4 / 模块 3、4）。

状态机：
  pending_review --confirm--> confirmed（入向量库）
  pending_review --reject --> rejected（不入库，仅留痕）
  confirmed     --edit-->    confirmed（version+1，重新向量化）
  pending_review --edit-->   pending_review（version+1，仍需确认）
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from ..core.embeddings import get_embedder
from ..models.tables import Commit, KnowledgeEntry, Repository
from ..vectorstore import KbChunk, get_vector_store

logger = logging.getLogger("ckp.knowledge")

STATUS_PENDING = "pending_review"
STATUS_CONFIRMED = "confirmed"
STATUS_REJECTED = "rejected"


class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    pass


def build_vector_text(entry: KnowledgeEntry) -> str:
    """检索主文本 = summary + 标签 + 模块 + 主要文件路径（方案：索引用自然语言，不索引原始 diff）。"""
    parts = [entry.summary or ""]
    if entry.tags:
        parts.append("标签: " + "、".join(entry.tags))
    if entry.modules:
        parts.append("模块: " + "、".join(entry.modules))
    if entry.background:
        parts.append("背景: " + entry.background)
    if entry.reusable_note:
        parts.append("可复用: " + entry.reusable_note)
    return "\n".join(p for p in parts if p.strip())


def index_entry(entry: KnowledgeEntry) -> str:
    """确认后向量化入库（重复调用 = 覆盖，实现重新向量化）。"""
    embedder = get_embedder()
    vec = embedder.embed_texts([build_vector_text(entry)])[0]
    store = get_vector_store()
    return store.add(
        KbChunk(
            vector_id=f"entry-{entry.id}-{uuid.uuid4().hex[:8]}",
            doc_id=str(entry.id),
            text=build_vector_text(entry),
            repo_id=entry.repo_id,
            tags=list(entry.tags or []),
            modules=list(entry.modules or []),
            file_paths=list(entry.main_paths or []),
            commit_hash=entry.commit.commit_hash if entry.commit else "",
            entry_version=entry.version,
        ),
        vec,
    )


def deindex_entry(entry_id: int) -> int:
    return get_vector_store().delete_by_doc(str(entry_id))


def apply_analysis(entry: KnowledgeEntry, analysis: dict) -> None:
    entry.summary = analysis["summary"]
    entry.background = analysis.get("background", "")
    entry.core_changes = analysis.get("core_changes", [])
    entry.modules = analysis.get("modules", [])
    entry.impact = analysis.get("impact", "")
    entry.tags = analysis.get("tags", [])
    entry.risk_note = analysis.get("risk_note", "")
    entry.reusable_note = analysis.get("reusable_note", "")


def _get_entry(session: Session, entry_id: int) -> KnowledgeEntry:
    entry = session.get(KnowledgeEntry, entry_id)
    if not entry:
        raise NotFoundError(f"知识条目 {entry_id} 不存在")
    return entry


def edit_entry(session: Session, entry_id: int, fields: dict, comment: str = "") -> KnowledgeEntry:
    """人工编辑（PATCH）：修正 LLM 总结后 version+1；已确认条目即时重新向量化。"""
    entry = _get_entry(session, entry_id)
    editable = {"summary", "background", "core_changes", "modules", "impact", "tags", "risk_note", "reusable_note"}
    changed = False
    for key in editable:
        if key in fields and fields[key] is not None:
            setattr(entry, key, fields[key])
            changed = True
    if not changed:
        return entry
    entry.version += 1
    entry.review_comment = comment or entry.review_comment
    if entry.status == STATUS_CONFIRMED:
        index_entry(entry)  # 已确认 → 立即重新向量化，向量与文本保持一致
    return entry


def confirm_entry(session: Session, entry_id: int, reviewed_by: str, comment: str = "") -> KnowledgeEntry:
    entry = _get_entry(session, entry_id)
    if entry.status == STATUS_CONFIRMED:
        raise ConflictError(f"条目 {entry_id} 已是确认状态，无需重复确认")
    if entry.status == STATUS_REJECTED:
        raise ConflictError(f"条目 {entry_id} 已被驳回；如需启用请先编辑重新提交")
    entry.status = STATUS_CONFIRMED
    entry.reviewed_by = reviewed_by
    entry.review_comment = comment
    index_entry(entry)  # 质量闸门：只有确认后才进向量库
    return entry


def reject_entry(session: Session, entry_id: int, reviewed_by: str, reason: str = "") -> KnowledgeEntry:
    entry = _get_entry(session, entry_id)
    if entry.status == STATUS_CONFIRMED:
        deindex_entry(entry.id)  # 允许撤回：从向量库移除
    entry.status = STATUS_REJECTED
    entry.reviewed_by = reviewed_by
    entry.review_comment = reason
    return entry


def entry_to_dict(entry: KnowledgeEntry, with_files: bool = False, session: Session | None = None) -> dict:
    commit = entry.commit
    data = {
        "id": entry.id,
        "repo_id": entry.repo_id,
        "commit_id": entry.commit_id,
        "commit_hash": commit.commit_hash if commit else "",
        "commit_message": commit.message if commit else "",
        "author": commit.author if commit else "",
        "committed_at": commit.committed_at.isoformat() if commit and commit.committed_at else "",
        "summary": entry.summary,
        "background": entry.background,
        "core_changes": entry.core_changes,
        "modules": entry.modules,
        "impact": entry.impact,
        "tags": entry.tags,
        "risk_note": entry.risk_note,
        "reusable_note": entry.reusable_note,
        "status": entry.status,
        "version": entry.version,
        "reviewed_by": entry.reviewed_by,
        "review_comment": entry.review_comment,
    }
    if with_files and session is not None and entry.commit_id:
        from ..models.tables import CommitFile

        rows = session.query(CommitFile).filter(CommitFile.commit_id == entry.commit_id).all()
        data["files"] = [
            {"path": r.path, "change_type": r.change_type, "additions": r.additions, "deletions": r.deletions}
            for r in rows
        ]
    return data
