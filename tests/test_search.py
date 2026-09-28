"""检索模块测试：向量召回 / 标签过滤 / 阈值 / 检索日志。"""
import pytest

from app.knowledge import service as ksvc
from app.retrieval.searcher import search


@pytest.fixture()
def confirmed_repo(db, synced_repo):
    repo, stats = synced_repo
    ids = []
    for eid in stats["entry_ids"]:
        ksvc.confirm_entry(db, eid, reviewed_by="t")
        ids.append(eid)
    db.commit()
    return repo, ids


def test_search_relevant_first(db, confirmed_repo):
    repo, ids = confirmed_repo
    r = search(db, repo.id, "我需要新增一个订单导出功能，参考以前导出和权限校验的做法", top_k=3)
    assert r["total"] > 0
    assert r["hits"][0]["score"] >= r["hits"][-1]["score"]
    top = r["hits"][0]
    assert top["summary"]
    assert top["commit"]["hash"]
    assert top["entry_id"] in ids


def test_search_writes_retrieval_log(db, confirmed_repo):
    from app.models.tables import RetrievalLog

    repo, _ = confirmed_repo
    before = db.query(RetrievalLog).count()
    r = search(db, repo.id, "数据库加字段怎么做的", top_k=2)
    assert db.query(RetrievalLog).count() == before + 1
    log = db.query(RetrievalLog).order_by(RetrievalLog.id.desc()).first()
    assert log.query_text == "数据库加字段怎么做的"
    assert log.hit_entry_ids == [h["entry_id"] for h in r["hits"]]


def test_search_tag_filter(db, confirmed_repo):
    repo, _ = confirmed_repo
    r = search(db, repo.id, "导出订单", top_k=5, tags=["权限"])
    assert all(h["tags"] and "权限" in h["tags"] for h in r["hits"])
    r_all = search(db, repo.id, "导出订单", top_k=5)
    assert r["total"] <= r_all["total"]


def test_search_empty_query(db, confirmed_repo):
    repo, _ = confirmed_repo
    with pytest.raises(ValueError):
        search(db, repo.id, "   ")
