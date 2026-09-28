"""M1 端到端演示：采集 → 分析 → 确认 → 入库 → 检索（CLI 直接驱动服务层）。

用法：
  python scripts/make_demo_repo.py            # 先生成演示仓库
  python scripts/demo_m1.py                   # 全流程演示（无 Key 走 mock 模式）

有 GLM Key 时：.env 配置 LLM_PROVIDER=glm / ZHIPU_API_KEY=xxx 后重跑，分析即为真实大模型输出。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.collector.sync import sync_repository  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import Base, get_engine, init_db  # noqa: E402
from app.knowledge import service as ksvc  # noqa: E402
from app.models.tables import Repository  # noqa: E402
from app.retrieval.searcher import search  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402


def sep(title: str) -> None:
    print(f"\n{'═' * 62}\n  {title}\n{'═' * 62}")


def main() -> None:
    init_db()
    Session = sessionmaker(bind=get_engine())
    db = Session()
    s = get_settings()
    sep(f"STEP 0 · 环境确认（LLM={s.llm_provider}, Embedding={s.embedding_provider}）")

    repo_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "demo_repo")
    if not os.path.exists(os.path.join(repo_path, ".git")):
        os.system(f'{sys.executable} "{os.path.join(os.path.dirname(os.path.abspath(__file__)), "make_demo_repo.py")}"')

    sep("STEP 1 · 登记仓库")
    repo = db.query(Repository).filter(Repository.git_url == repo_path).first()  # 幂等：同地址复用
    if repo is None:
        repo = Repository(name="demo-order-system", git_url=repo_path, default_branch="main", language="java")
        db.add(repo)
        db.flush()
    db.add(repo)
    db.commit()
    print(f"  repo_id={repo.id}  name={repo.name}  path={repo_path}")

    sep("STEP 2 · 同步采集 + LLM 分析（commit message 全是无效描述，由模型从 diff 还原语义）")
    stats = sync_repository(db, repo, mode="backfill")
    print(f"  扫描 {stats['scanned']} 个提交: 新增 {stats['new']} / merge跳过 {stats['skipped_merge']} / "
          f"噪声跳过 {stats['skipped_noise']} / 分析失败 {stats['failed']}")
    print(f"  → 生成待确认知识条目 {len(stats['entry_ids'])} 个: {stats['entry_ids']}")

    sep("STEP 3 · 查看待确认条目（人审工作台视角）")
    for eid in stats["entry_ids"]:
        d = ksvc.entry_to_dict(db.get(ksvc.KnowledgeEntry, eid))
        print(f"\n  [entry {d['id']}] {d['summary']}")
        print(f"    标签: {d['tags']}  模块: {d['modules']}")
        print(f"    风险: {d['risk_note'] or '-'}")
        print(f"    可复用: {d['reusable_note'] or '-'}")

    sep("STEP 4 · 人工确认（质量闸门：确认后才进入正式知识库/向量库）")
    confirmed_ids = []
    for eid in stats["entry_ids"]:
        entry = ksvc.confirm_entry(db, eid, reviewed_by="zhang.san", comment="demo 自动确认")
        confirmed_ids.append(entry.id)
        print(f"  entry {entry.id} → {entry.status} (v{entry.version}) 已向量化")

    sep("STEP 5 · 新需求检索（自然语言 → 历史相似提交）")
    queries = [
        ("我需要新增一个订单导出功能，参考以前导出和权限校验的做法", ["权限"]),
        ("项目里以前有没有缓存相关的配置和实现", None),
        ("要给订单表加字段，看看之前数据库变更是怎么做的", None),
    ]
    for q, tags in queries:
        r = search(db, repo.id, q, top_k=3, tags=tags, created_by="demo")
        print(f"\n  需求: {q}" + (f"  [标签过滤={tags}]" if tags else ""))
        print(f"  延迟 {r['latency_ms']}ms, 命中 {r['total']} 条:")
        for h in r["hits"]:
            print(f"    - entry {h['entry_id']} (score={h['score']}) {h['summary'][:44]}…")

    sep("STEP 6 · 统计")
    from app.api.routes import stats as _stats  # 复用统计逻辑

    print("  " + json.dumps(_stats(db)["data"], ensure_ascii=False))
    db.close()
    print("\nM1 闭环演示完成 ✔  （API 方式见 README：uvicorn app.main:app --port 8100）")


if __name__ == "__main__":
    main()
