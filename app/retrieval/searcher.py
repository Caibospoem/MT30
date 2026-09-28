"""需求理解与检索模块（对应技术方案模块 5；混合检索 M2 升级 BM25+重排）。

M1：查询向量化 → 向量库 Top-K（repo_id 精确过滤 + 标签过滤）→ 低于阈值丢弃 →
写 RetrievalLog（评测命中率的数据底座）。
"""
from __future__ import annotations

import logging
import time

from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.embeddings import get_embedder
from ..knowledge.service import build_vector_text  # noqa: F401  (保持与入库文本同源)
from ..models.tables import Commit, CommitFile, KnowledgeEntry, RetrievalLog
from ..vectorstore import get_vector_store

logger = logging.getLogger("ckp.retrieval")


def search(session: Session, repo_id: int, query: str, top_k: int | None = None,
           tags: list[str] | None = None, created_by: str = "") -> dict:
    settings = get_settings()
    query = (query or "").strip()
    if not query:
        raise ValueError("query 不能为空")
    top_k = min(top_k or settings.retrieval_top_k, 20)

    t0 = time.time()
    qvec = get_embedder().embed_texts([query])[0]
    raw_hits = get_vector_store().search(qvec, top_k=top_k, repo_id=repo_id, tags=tags)

    hits = []
    for chunk, score in raw_hits:
        if score < settings.retrieval_min_score:  # 阈值过滤：低于阈值的弱相关不返回
            continue
        entry = session.get(KnowledgeEntry, int(chunk.doc_id))
        if not entry or entry.status != "confirmed":
            continue
        commit = session.get(Commit, entry.commit_id) if entry.commit_id else None
        files = []
        if commit:
            rows = (session.query(CommitFile.path)
                    .filter(CommitFile.commit_id == commit.id)
                    .order_by(CommitFile.id).limit(10).all())
            files = [r[0] for r in rows]
        hits.append({
            "entry_id": entry.id,
            "score": round(score, 4),
            "summary": entry.summary,
            "background": entry.background,
            "tags": entry.tags,
            "modules": entry.modules,
            "risk_note": entry.risk_note,
            "reusable_note": entry.reusable_note,
            "commit": {
                "hash": commit.commit_hash if commit else chunk.commit_hash,
                "message": commit.message if commit else "",
                "author": commit.author if commit else "",
                "committed_at": commit.committed_at.isoformat() if commit and commit.committed_at else "",
            },
            "files": files,
        })

    latency_ms = int((time.time() - t0) * 1000)
    log = RetrievalLog(
        repo_id=repo_id, query_text=query, top_k=top_k,
        hit_entry_ids=[h["entry_id"] for h in hits],
        scores=[h["score"] for h in hits],
        tags_used=tags or [],
        latency_ms=latency_ms, created_by=created_by,
    )
    session.add(log)
    session.flush()

    return {
        "log_id": log.id,
        "latency_ms": latency_ms,
        "total": len(hits),
        "hits": hits,
    }
