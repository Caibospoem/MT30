"""Embedding 抽象层（与 compliance-platform 同模式）。

- local: 特征哈希（hashing trick）字符 n-gram TF 向量，中文友好、零网络依赖，
  演示与中小规模知识库够用；换 embedding-3 仅需配置 Key。
- openai: OpenAI 兼容 /embeddings 接口（GLM embedding-3 适用），带重试。
"""
from __future__ import annotations

import hashlib
import math
import re

import httpx
import numpy as np

from ..config import get_settings

_DIM = 512  # local 模式固定维度


def _tokenize(text: str) -> list[str]:
    """中英混合分词：中文取 2/3-gram，英文/数字取词。"""
    text = text.lower().strip()
    tokens: list[str] = []
    for seg in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", text):
        if seg.isascii():
            tokens.append(seg)
        else:
            seg = f"^{seg}$"
            tokens.extend(seg[i:i + 2] for i in range(len(seg) - 1))
            tokens.extend(seg[i:i + 3] for i in range(len(seg) - 2))
    return tokens or ["<empty>"]


def _hash_feature(token: str) -> int:
    return int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)


class LocalEmbedder:
    name = "local-hashing-512"

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        vectors = np.zeros((len(texts), _DIM), dtype=np.float32)
        for i, text in enumerate(texts):
            counts: dict[int, float] = {}
            for token in _tokenize(text):
                h = _hash_feature(token)
                idx, sign = h % _DIM, (1 if (h >> 64) & 1 else -1)
                counts[idx] = counts.get(idx, 0.0) + sign
            for idx, value in counts.items():
                vectors[i, idx] = value
            norm = float(np.linalg.norm(vectors[i]))
            if norm > 0:
                vectors[i] /= norm
        return vectors


class OpenAIEmbedder:
    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.name = f"openai:{model}"

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=60) as client:
                    resp = client.post(
                        f"{self.base_url}/embeddings",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json={"model": self.model, "input": texts},
                    )
                    if resp.status_code == 429 or resp.status_code >= 500:
                        raise httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
                    resp.raise_for_status()
                    data = resp.json()["data"]
                    return np.array([d["embedding"] for d in data], dtype=np.float32)
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
        raise RuntimeError(f"Embedding API 重试后仍失败: {last_exc}")


def get_embedder():
    s = get_settings()
    if s.embedding_provider == "openai":
        key = s.openai_api_key or s.zhipu_api_key
        base = s.openai_base_url if s.openai_api_key else "https://open.bigmodel.cn/api/paas/v4"
        if key:
            return OpenAIEmbedder(key, base, s.embedding_model)
    return LocalEmbedder()
