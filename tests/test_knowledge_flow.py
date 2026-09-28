"""知识条目状态机测试：确认 / 驳回 / 编辑重嵌入 / 幂等冲突。"""
import pytest

from app.collector.sync import sync_repository
from app.knowledge import service as ksvc
from app.models.tables import Repository
from app.vectorstore import get_vector_store


def _mk_repo(db, path):
    repo = Repository(name="r", git_url=path, default_branch="main", language="java")
    db.add(repo)
    db.flush()
    return repo


def test_sync_creates_pending_entries(db, demo_repo):
    repo = _mk_repo(db, demo_repo)
    stats = sync_repository(db, repo, mode="backfill")
    assert stats["scanned"] == 5
    assert stats["new"] == 5 and stats["analyzed"] == 5
    assert stats["failed"] == 0
    entries = db.query(ksvc.KnowledgeEntry).all()
    assert len(entries) == 5
    assert all(e.status == "pending_review" for e in entries)
    assert all(e.summary for e in entries)


def test_sync_idempotent(db, demo_repo):
    repo = _mk_repo(db, demo_repo)
    s1 = sync_repository(db, repo)
    s2 = sync_repository(db, repo)
    assert s2["new"] == 0


def test_confirm_indexes_vector(db, synced_repo):
    repo, stats = synced_repo
    eid = stats["entry_ids"][0]
    entry = ksvc.confirm_entry(db, eid, reviewed_by="zhang.san")
    assert entry.status == "confirmed"
    assert entry.reviewed_by == "zhang.san"
    store = get_vector_store()
    assert store.count() == 1

    with pytest.raises(ksvc.ConflictError):
        ksvc.confirm_entry(db, eid, reviewed_by="again")


def test_reject_not_indexed(db, synced_repo):
    repo, stats = synced_repo
    eid = stats["entry_ids"][0]
    entry = ksvc.reject_entry(db, eid, reviewed_by="li.si", reason="总结不准")
    assert entry.status == "rejected"
    assert get_vector_store().count() == 0


def test_edit_pending_then_confirm(db, synced_repo):
    repo, stats = synced_repo
    eid = stats["entry_ids"][0]
    edited = ksvc.edit_entry(db, eid, {"summary": "人工修正后的总结", "tags": ["权限"]}, comment="补充")
    assert edited.version == 2
    assert edited.status == "pending_review"
    assert edited.summary == "人工修正后的总结"

    confirmed = ksvc.confirm_entry(db, eid, reviewed_by="zhang.san")
    assert confirmed.version == 2
    assert get_vector_store().count() == 1


def test_edit_confirmed_reindexes(db, synced_repo):
    repo, stats = synced_repo
    eid = stats["entry_ids"][0]
    ksvc.confirm_entry(db, eid, reviewed_by="a")
    assert get_vector_store().count() == 1
    ksvc.edit_entry(db, eid, {"summary": "确认后修改，应触发重新向量化"}, comment="")
    store = get_vector_store()
    assert store.count() == 1  # 删旧插新，总量不变
    assert store._chunks[0].entry_version == 2  # sync v1 → 编辑后 v2


def test_entry_not_found(db):
    with pytest.raises(ksvc.NotFoundError):
        ksvc.confirm_entry(db, 99999, reviewed_by="x")
