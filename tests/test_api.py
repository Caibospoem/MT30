"""API 层测试（TestClient 全链路）。"""
import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app


@pytest.fixture()
def client(db, demo_repo):
    app.dependency_overrides[get_session] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["data"]["llm_provider"]


def test_full_loop(client, demo_repo):
    # 1. 登记仓库
    r = client.post("/api/v1/repositories", json={
        "name": "demo-order", "git_url": demo_repo, "default_branch": "main", "language": "java"})
    assert r.status_code == 200
    repo_id = r.json()["data"]["id"]

    # 2. 同步（采集+分析）
    r = client.post(f"/api/v1/repositories/{repo_id}/sync", json={"mode": "backfill"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["scanned"] == 5 and data["new"] == 5
    entry_id = data["entry_ids"][0]

    # 3. 待确认列表
    r = client.get("/api/v1/knowledge", params={"repo_id": repo_id, "status": "pending_review"})
    assert r.json()["data"]["total"] == 5

    # 4. 详情含文件清单
    r = client.get(f"/api/v1/knowledge/{entry_id}")
    detail = r.json()["data"]
    assert detail["summary"]
    assert detail["files"]

    # 5. 人工编辑 → 确认入库
    r = client.patch(f"/api/v1/knowledge/{entry_id}",
                     json={"summary": "新增订单导出接口，含管理员权限校验", "tags": ["权限", "接口"], "review_comment": "修正"})
    assert r.json()["data"]["version"] == 2
    r = client.post(f"/api/v1/knowledge/{entry_id}/confirm", json={"reviewed_by": "zhang.san"})
    assert r.json()["data"]["status"] == "confirmed"

    # 6. 检索
    r = client.post("/api/v1/search", json={"repo_id": repo_id, "query": "新增导出功能，参考权限校验方式", "top_k": 3})
    hits = r.json()["data"]["hits"]
    assert hits and hits[0]["entry_id"] == entry_id

    # 7. 统计
    r = client.get("/api/v1/stats")
    d = r.json()["data"]
    assert d["entries_confirmed"] == 1 and d["vectors"] == 1


def test_api_errors(client, demo_repo):
    assert client.get("/api/v1/knowledge/999").status_code == 404
    assert client.post("/api/v1/search", json={"repo_id": 1, "query": " "}).status_code == 400

    r = client.post("/api/v1/repositories", json={"name": "x", "git_url": demo_repo})
    repo_id = r.json()["data"]["id"]
    client.post(f"/api/v1/repositories/{repo_id}/sync", json={})
    # 重复确认 → 409
    listing = client.get("/api/v1/knowledge", params={"status": "pending_review"}).json()["data"]
    eid = listing["items"][0]["id"]
    client.post(f"/api/v1/knowledge/{eid}/confirm", json={"reviewed_by": "a"})
    r = client.post(f"/api/v1/knowledge/{eid}/confirm", json={"reviewed_by": "b"})
    assert r.status_code == 409
    # 不存在的仓库同步 → 404
    assert client.post("/api/v1/repositories/999/sync", json={}).status_code == 404
