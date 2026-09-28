"""LLM / Embedding 连通性自检（沿用 compliance-platform 经验）。

用法：python scripts/check_llm.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.embeddings import get_embedder  # noqa: E402
from app.core.llm import get_llm  # noqa: E402


def main() -> int:
    s = get_settings_from_env()
    print(f"LLM provider = {s}")
    llm = get_llm()
    out = llm.chat(task="ping", system="你是回声器", user="回复 pong", json_mode=False)
    print(f"chat  连通: {out[:40]!r}")
    emb = get_embedder()
    vec = emb.embed_texts(["连通性测试"])[0]
    print(f"embed 连通: provider={emb.name} dim={vec.shape[0]}")
    print("OK")
    return 0


def get_settings_from_env():
    from app.config import get_settings

    s = get_settings()
    print(json.dumps({"llm_provider": s.llm_provider, "llm_model": s.llm_model,
                      "embedding_provider": s.embedding_provider}, ensure_ascii=False))
    return s.llm_provider


if __name__ == "__main__":
    sys.exit(main())
