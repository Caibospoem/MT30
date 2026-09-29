"""嵌入模型对比探针：同一组语义改写问题，在不同 Embedding Provider 下的原始检索分数。

用途：升级 embedding-3 前后各跑一次，对比排序与分数分布，为 M2 技术选型留数据。

用法：
  python scripts/probe_embedding.py

说明：
  - 直接查向量库取原始 Top-5，不走阈值过滤（阈值是按 local-hash 调的，
    换嵌入模型后分数尺度不同，屏蔽原始分数会误判）；
  - 只读检索，不写 RetrievalLog，不影响 demo/评测数据；
  - 需先跑过 demo_m1.py（已有已确认条目与向量索引）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core.embeddings import get_embedder  # noqa: E402
from app.db import get_engine, init_db  # noqa: E402
from app.knowledge import service as ksvc  # noqa: E402
from app.models.tables import KnowledgeEntry, Repository  # noqa: E402
from app.vectorstore import get_vector_store  # noqa: E402

# 语义探针：刻意用"换说法"的需求描述，字面重合少、语义重合多
#   P1 权限/鉴权语义（目标 entry5）；P2 大数据量导出→异步/线程池（目标 5、2）；
#   P3 避免全表捞取→全表扫描缺陷/缓存（目标 4、3）；P4 对照组：字面就能中（目标 2）；
#   P5 无目标校准题（知识库无前端条目，看"不相关"的分数长什么样）
PROBES = [
    ("P1", "调用导出功能前怎么校验操作人的身份"),
    ("P2", "新增报表导出时数据量大会不会拖垮服务，之前有类似设计吗"),
    ("P3", "以后查询接口要怎么避免把整张表都捞出来"),
    ("P4", "想把应用参数挪到配置中心，之前有类似改动吗"),
    ("P5", "前端页面怎么加一个下拉多选框"),
]


def main() -> None:
    init_db()
    db = sessionmaker(bind=get_engine())()
    s = get_settings()
    embedder = get_embedder()
    store = get_vector_store()

    repo = db.query(Repository).first()
    if repo is None:
        print("未找到仓库：请先运行 make_demo_repo.py + demo_m1.py 建库")
        return

    print(f"Embedding provider = {embedder.name}")
    print(f"当前生效阈值 = {s.effective_min_score}（provider={s.embedding_provider}，探针不做过滤）")
    print(f"repo_id = {repo.id}\n")

    for label, query in PROBES:
        print(f"[{label}] {query}")
        qvec = embedder.embed_texts([query])[0]
        raw = store.search(qvec, top_k=5, repo_id=repo.id)
        if not raw:
            print("    （无结果）\n")
            continue
        for rank, (chunk, score) in enumerate(raw, 1):
            entry = db.get(KnowledgeEntry, int(chunk.doc_id))
            name = entry.summary[:38] if entry else "?"
            tags = "、".join(entry.tags) if entry and entry.tags else "-"
            mark = "过阈值" if score >= s.retrieval_min_score else "未过阈值"
            print(f"    {rank}. score={score:.4f} {mark}  entry {chunk.doc_id} [{tags}] {name}")
        print()

    db.close()
    print("探针完成 [OK]  升级前后各跑一次，保存输出用于对比")


if __name__ == "__main__":
    main()
