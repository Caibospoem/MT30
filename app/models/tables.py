"""ORM 模型：repository / commit / commit_file / knowledge_entry / retrieval_log。

对应技术方案 3.2 节数据模型（M1 裁剪：generation_suggestion 属 M2）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Repository(Base):
    __tablename__ = "repository"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    git_url: Mapped[str] = mapped_column(String(512))  # 支持 https:// 或本地路径
    default_branch: Mapped[str] = mapped_column(String(64), default="main")
    language: Mapped[str] = mapped_column(String(32), default="java")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    commits: Mapped[list["Commit"]] = relationship(back_populates="repository")


class Commit(Base):
    __tablename__ = "commit"
    __table_args__ = (UniqueConstraint("repo_id", "commit_hash", name="uq_commit"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repository.id"), index=True)
    commit_hash: Mapped[str] = mapped_column(String(40))
    author: Mapped[str] = mapped_column(String(128), default="")
    author_email: Mapped[str] = mapped_column(String(256), default="")
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    message: Mapped[str] = mapped_column(Text, default="")
    parents: Mapped[list] = mapped_column(JSON, default=list)  # 父提交 hash（判 merge）
    files_changed: Mapped[int] = mapped_column(Integer, default=0)
    insertions: Mapped[int] = mapped_column(Integer, default=0)
    deletions: Mapped[int] = mapped_column(Integer, default=0)
    is_merge: Mapped[bool] = mapped_column(Boolean, default=False)
    # pending=待分析 done=已产出知识条目 skipped=被过滤 failed=分析失败
    process_status: Mapped[str] = mapped_column(String(16), default="pending", index=True)

    repository: Mapped[Repository] = relationship(back_populates="commits")
    files: Mapped[list["CommitFile"]] = relationship(back_populates="commit", cascade="all,delete-orphan")
    knowledge: Mapped["KnowledgeEntry | None"] = relationship(back_populates="commit", uselist=False)


class CommitFile(Base):
    __tablename__ = "commit_file"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    commit_id: Mapped[int] = mapped_column(ForeignKey("commit.id"), index=True)
    path: Mapped[str] = mapped_column(String(1024))
    change_type: Mapped[str] = mapped_column(String(1), default="M")  # A/M/D/R
    old_path: Mapped[str | None] = mapped_column(String(1024), default=None)
    language: Mapped[str] = mapped_column(String(32), default="")  # 按扩展名推断
    diff_text: Mapped[str] = mapped_column(Text, default="")
    additions: Mapped[int] = mapped_column(Integer, default=0)
    deletions: Mapped[int] = mapped_column(Integer, default=0)

    commit: Mapped[Commit] = relationship(back_populates="files")


class KnowledgeEntry(Base):
    """知识条目：一次提交的结构化自然语言知识（检索主文本 = summary + tags）。"""

    __tablename__ = "knowledge_entry"
    __table_args__ = (Index("ix_ke_status", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    commit_id: Mapped[int] = mapped_column(ForeignKey("commit.id"), unique=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repository.id"), index=True)

    # —— LLM 产出 / 人工可编辑字段 ——
    summary: Mapped[str] = mapped_column(Text, default="")            # 变更总结（检索主文本）
    background: Mapped[str] = mapped_column(Text, default="")         # 业务背景
    core_changes: Mapped[list] = mapped_column(JSON, default=list)    # [{file, change, why}]
    modules: Mapped[list] = mapped_column(JSON, default=list)         # 涉及模块
    impact: Mapped[str] = mapped_column(Text, default="")             # 业务影响
    tags: Mapped[list] = mapped_column(JSON, default=list)            # 固定枚举标签
    risk_note: Mapped[str] = mapped_column(Text, default="")          # 风险提示
    reusable_note: Mapped[str] = mapped_column(Text, default="")      # 可复用经验

    # —— 状态机：pending_review → confirmed / rejected；confirmed 编辑后版本+1 重新入库 ——
    status: Mapped[str] = mapped_column(String(16), default="pending_review")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(String(128), default="")  # 谁触发入库（sync/人工）
    review_comment: Mapped[str] = mapped_column(Text, default="")
    reviewed_by: Mapped[str] = mapped_column(String(128), default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    indexed: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否已写入向量库
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    @property
    def main_paths(self) -> list[str]:
        """向量化元数据用：该提交涉及的主要文件路径（最多 5 个）。"""
        try:
            files = self.commit.files if self.commit else []
            return [f.path for f in files][:5]
        except Exception:
            return []

    commit: Mapped[Commit] = relationship(back_populates="knowledge")


class RetrievalLog(Base):
    """检索日志：评测命中率（hit_flags 人工标注）的数据底座。"""

    __tablename__ = "retrieval_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repository.id"), index=True)
    query_text: Mapped[str] = mapped_column(Text)
    top_k: Mapped[int] = mapped_column(Integer, default=5)
    hit_entry_ids: Mapped[list] = mapped_column(JSON, default=list)
    scores: Mapped[list] = mapped_column(JSON, default=list)
    tags_used: Mapped[list] = mapped_column(JSON, default=list)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
