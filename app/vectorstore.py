"""轻量向量库：numpy 余弦检索 + NPZ 持久化（与 compliance-platform 同模式）。

接口与 Chroma/FAISS 对齐（add / delete_by_doc / search / count），M2 切换
Chroma/pgvector 时只需替换本模块实现，retrieval 层零改动。
每条记录 = 一个知识条目的语义向量 + 过滤元数据。
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .config import get_settings

logger = logging.getLogger("ckp.vectorstore")


@dataclass
class KbChunk:
    vector_id: str
    doc_id: str            # knowledge_entry.id
    text: str              # 向量化文本（summary+标签+模块+文件）
    repo_id: int = 0
    tags: list = field(default_factory=list)
    modules: list = field(default_factory=list)
    file_paths: list = field(default_factory=list)
    commit_hash: str = ""
    entry_version: int = 1


def _normalize(vectors: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vectors, axis=-1, keepdims=True)
    norm[norm == 0] = 1.0
    return vectors / norm


class KnowledgeVectorStore:
    def __init__(self, persist_dir: Path | None = None):
        self.persist_dir = Path(persist_dir) if persist_dir else get_settings().vector_dir
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._chunks: list[KbChunk] = []
        self._matrix: np.ndarray | None = None
        self._load()

    # ── 基础 ──
    def count(self) -> int:
        return len(self._chunks)

    def add(self, chunk: KbChunk, vector: np.ndarray) -> str:
        """写入/覆盖一个知识条目向量（同 doc_id 先删后加，实现重新向量化）。"""
        with self._lock:
            self.delete_by_doc(chunk.doc_id, _internal=True)
            vec = _normalize(vector.reshape(1, -1))[0]
            if self._matrix is None:
                self._matrix = vec.reshape(1, -1)
            else:
                if self._matrix.shape[1] != vec.shape[0]:
                    raise ValueError(f"向量维度不一致: 库内 {self._matrix.shape[1]} vs 新 {vec.shape[0]}")
                self._matrix = np.vstack([self._matrix, vec])
            self._chunks.append(chunk)
            self._persist()
            return chunk.vector_id

    def delete_by_doc(self, doc_id: str, _internal: bool = False) -> int:
        with self._lock if not _internal else _NullCtx():
            keep_idx = [i for i, c in enumerate(self._chunks) if c.doc_id != doc_id]
            removed = len(self._chunks) - len(keep_idx)
            if removed:
                self._chunks = [self._chunks[i] for i in keep_idx]
                self._matrix = self._matrix[keep_idx] if self._matrix is not None and len(keep_idx) else (
                    self._matrix[keep_idx] if self._matrix is not None else None)
                self._persist()
            return removed

    def search(self, query_vec: np.ndarray, top_k: int = 5,
               repo_id: int | None = None, tags: list[str] | None = None) -> list[tuple[KbChunk, float]]:
        """余弦 Top-K；repo_id 精确过滤，tags 要求条目标签包含全部指定标签。"""
        if self._matrix is None or not self._chunks:
            return []
        q = _normalize(query_vec.reshape(1, -1))[0]
        scores = self._matrix @ q
        order = np.argsort(-scores)
        hits: list[tuple[KbChunk, float]] = []
        for idx in order:
            chunk = self._chunks[int(idx)]
            if repo_id is not None and chunk.repo_id != repo_id:
                continue
            if tags and not all(t in chunk.tags for t in tags):
                continue
            hits.append((chunk, float(scores[idx])))
            if len(hits) >= top_k:
                break
        return hits

    # ── 持久化 ──
    def _persist(self) -> None:
        meta_path = self.persist_dir / "meta.json"
        vec_path = self.persist_dir / "vectors.npy"
        meta_path.write_text(json.dumps([asdict(c) for c in self._chunks], ensure_ascii=False), encoding="utf-8")
        if self._matrix is not None:
            np.save(vec_path, self._matrix)

    def _load(self) -> None:
        meta_path = self.persist_dir / "meta.json"
        vec_path = self.persist_dir / "vectors.npy"
        if not (meta_path.exists() and vec_path.exists()):
            return
        try:
            self._chunks = [KbChunk(**m) for m in json.loads(meta_path.read_text(encoding="utf-8"))]
            self._matrix = np.load(vec_path)
            logger.info("向量库已加载: %d 条, dim=%d", len(self._chunks), self._matrix.shape[1])
        except Exception:
            logger.exception("向量库加载失败，将以空库启动")
            self._chunks, self._matrix = [], None


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


_STORE: KnowledgeVectorStore | None = None


def get_vector_store() -> KnowledgeVectorStore:
    global _STORE
    if _STORE is None:
        _STORE = KnowledgeVectorStore()
    return _STORE


def reset_vector_store() -> None:
    """测试辅助：切换目录后重建单例。"""
    global _STORE
    _STORE = None
