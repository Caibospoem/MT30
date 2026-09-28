"""同步编排服务：登记仓库 → 枚举提交 → 过滤入库 → LLM 分析 → 待确认条目。

M1 为同步阻塞实现（演示仓库规模小）；回灌大仓库时由 API 层提示分批（limit）。
幂等：按 (repo_id, commit_hash) 去重，重复 sync 自动跳过已处理提交。
"""
from __future__ import annotations

import logging
import os

from sqlalchemy.orm import Session

from ..analysis.analyzer import AnalysisError, analyze_commit
from ..collector import git_collector as gc
from ..config import get_settings
from ..knowledge.service import STATUS_PENDING, apply_analysis
from ..models.tables import Commit, CommitFile, KnowledgeEntry, Repository
from ..preprocessing.diff_filter import is_formatting_only, is_noise_path

logger = logging.getLogger("ckp.sync")


class SyncError(Exception):
    pass


def _repo_dir(repo_id: int) -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "repos", str(repo_id))


def sync_repository(session: Session, repo: Repository, mode: str = "incremental",
                    since: str | None = None, until: str | None = None,
                    branch: str | None = None, limit: int | None = None) -> dict:
    """执行一次同步，返回统计摘要。mode 仅影响缺省参数（M1 语义一致，均全量枚举去重）。"""
    settings = get_settings()
    limit = min(limit or settings.sync_max_commits, settings.sync_max_commits)
    branch = branch or repo.default_branch

    try:
        local_path = gc.ensure_local_repo(repo.git_url, _repo_dir(repo.id), branch)
    except gc.CollectorError as exc:
        raise SyncError(str(exc)) from exc

    try:
        metas = gc.list_commits(local_path, branch=branch, since=since, until=until, limit=limit)
    except gc.CollectorError as exc:
        raise SyncError(str(exc)) from exc

    stats = {"scanned": len(metas), "new": 0, "skipped_existing": 0, "skipped_merge": 0,
             "skipped_noise": 0, "analyzed": 0, "failed": 0, "entry_ids": []}

    for meta in metas:
        exists = session.query(Commit.id).filter(
            Commit.repo_id == repo.id, Commit.commit_hash == meta["hash"]).first()
        if exists:
            stats["skipped_existing"] += 1
            continue

        if len(meta["parents"]) > 1:  # merge 提交：直接登记为 skipped，不建知识条目
            stats["skipped_merge"] += 1
            continue

        try:
            raw = gc.load_commit_detail(local_path, meta)
        except gc.CollectorError as exc:
            logger.warning("提交 %s 详情获取失败: %s", meta["hash"][:8], exc)
            stats["failed"] += 1
            continue

        commit = Commit(
            repo_id=repo.id, commit_hash=raw.hash, author=raw.author, author_email=raw.author_email,
            committed_at=raw.committed_at, message=raw.message, parents=raw.parents,
            files_changed=len(raw.files),
            insertions=sum(f.additions for f in raw.files),
            deletions=sum(f.deletions for f in raw.files),
            is_merge=False, process_status="pending",
        )
        session.add(commit)
        session.flush()  # 取 commit.id

        for f in raw.files:
            noisy = is_noise_path(f.path) or is_formatting_only(f.diff)
            session.add(CommitFile(
                commit_id=commit.id, path=f.path, old_path=f.old_path, change_type=f.change_type,
                additions=f.additions, deletions=f.deletions,
                language=_ext(f.path), diff_text="" if noisy else f.diff[:20000],
            ))

        try:
            analysis, payload, _truncated = analyze_commit(raw, settings)
        except AnalysisError as exc:
            if "噪声" in str(exc) or "格式化" in str(exc):
                commit.process_status = "skipped"
                stats["skipped_noise"] += 1
            else:
                commit.process_status = "failed"
                stats["failed"] += 1
                logger.warning("提交 %s 分析失败: %s", raw.hash[:8], exc)
            continue

        entry = KnowledgeEntry(
            repo_id=repo.id, commit_id=commit.id, status=STATUS_PENDING,
            summary="", created_by="sync", version=1,
        )
        apply_analysis(entry, analysis)
        session.add(entry)
        session.flush()  # 取 entry.id
        commit.process_status = "done"
        stats["new"] += 1
        stats["analyzed"] += 1
        stats["entry_ids"].append(entry.id)

    return stats


def _ext(path: str) -> str:
    name = os.path.basename(path)
    return name.rsplit(".", 1)[-1] if "." in name else ""
