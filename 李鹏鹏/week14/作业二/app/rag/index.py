"""检索索引：BM25 为主，向量检索为可选增强。

为什么默认用 BM25
-----------------
中文文档问答的召回，BM25 + 分词已经相当可靠，而且零模型下载、毫秒级响应，
非常适合「克隆即跑」的作业演示。需要更强语义召回时，设置
``RETRIEVAL_MODE=hybrid`` + ``EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5`` 即可启用
句向量；设置 ``MULTIMODAL_EMBEDDING_MODEL=clip-ViT-B-32`` 还能让文字直接检索图片。

索引新鲜度
----------
解析工作进程可能是独立进程，索引不能只依赖内存通知。这里用
``chunks`` 表的 ``(行数, 最大 rowid)`` 做指纹，每次检索前轻量比对，
变化后自动重建，天然支持多进程部署。
"""
from __future__ import annotations

import logging
import math
import re
import threading
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ..config import settings

logger = logging.getLogger(__name__)

_ASCII_TOKEN = re.compile(r"[a-z0-9][a-z0-9_+\-.]*")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_CJK_TOKEN = re.compile(r"^[\u4e00-\u9fff]+$")

_STOPWORDS = {
    "的", "了", "和", "是", "在", "我", "有", "就", "不", "人", "都", "一", "一个",
    "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好",
    "自己", "这", "那", "与", "及", "或", "请", "把", "被", "对", "为", "等", "其中",
    "什么", "哪些", "哪个", "如何", "为什么", "怎么", "怎样", "介绍", "说明", "一下",
    "以及", "可以", "这个", "那个", "这些", "那些", "是否", "还有", "并且", "但是",
    "所以", "如果", "因为", "然后", "我们", "他们", "它", "您",
}

_jieba_module: Any = None
_jieba_ready = False
_jieba_lock = threading.Lock()


def _ensure_jieba() -> Any:
    global _jieba_module, _jieba_ready
    if _jieba_ready:
        return _jieba_module
    with _jieba_lock:
        if not _jieba_ready:
            try:
                import jieba

                jieba.setLogLevel(logging.WARNING)
                jieba.initialize()
                _jieba_module = jieba
            except ModuleNotFoundError:  # pragma: no cover - 可选依赖
                _jieba_module = None
                logger.warning("未安装 jieba，退化为字符二元组分词")
            _jieba_ready = True
    return _jieba_module


def tokenize(text: str) -> list[str]:
    """中英文混合分词；没有 jieba 时用字符二元组兜底。"""
    text = (text or "").lower()
    if not text.strip():
        return []

    tokens: list[str] = []
    jieba = _ensure_jieba()
    if jieba is not None:
        for raw in jieba.lcut_for_search(text):
            token = raw.strip()
            if not token or token in _STOPWORDS:
                continue
            if _CJK_TOKEN.match(token) or _ASCII_TOKEN.fullmatch(token):
                tokens.append(token)
    else:
        tokens.extend(_ASCII_TOKEN.findall(text))
        for run in _CJK_RUN.findall(text):
            if len(run) == 1:
                tokens.append(run)
            else:
                tokens.extend(run[index:index + 2] for index in range(len(run) - 1))
    return tokens


def _chunk_text(chunk: dict[str, Any]) -> str:
    parts = [
        str(chunk.get("filename") or ""),
        str(chunk.get("section") or ""),
        str(chunk.get("caption") or ""),
        str(chunk.get("content") or ""),
    ]
    return "\n".join(part for part in parts if part)


@dataclass(slots=True)
class Hit:
    chunk: dict[str, Any]
    score: float = 0.0
    bm25: float = 0.0
    dense: float = 0.0
    clip: float = 0.0
    matched: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return str(self.chunk.get("id", ""))

    def as_source(self, rank: int) -> dict[str, Any]:
        chunk = self.chunk
        return {
            "rank": rank,
            "chunk_id": self.id,
            "doc_id": chunk.get("doc_id", ""),
            "filename": chunk.get("filename", ""),
            "page": int(chunk.get("page") or 1),
            "page_end": int(chunk.get("page_end") or chunk.get("page") or 1),
            "kind": chunk.get("kind", "text"),
            "section": chunk.get("section", ""),
            "caption": chunk.get("caption", ""),
            "snippet": " ".join(str(chunk.get("content") or "").split())[:260],
            "image_url": chunk.get("image_url", ""),
            "score": round(self.score, 4),
        }


class RagIndex:
    """线程安全的 BM25 / 混合索引。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._chunks: list[dict[str, Any]] = []
        self._tokens: list[list[str]] = []
        self._df: Counter[str] = Counter()
        self._avgdl = 0.0
        self._fingerprint: tuple[int, int] | None = None
        self._dense_model: Any = None
        self._dense_vectors: Any = None
        self._dense_ready = False
        self._clip_model: Any = None
        self._clip_vectors: Any = None
        self._clip_ready = False
        self._clip_failed = False
        self._embedding_failed = False

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def rebuild(self, chunks: Sequence[dict[str, Any]]) -> None:
        with self._lock:
            self._chunks = [dict(item) for item in chunks]
            self._tokens = [tokenize(_chunk_text(chunk)) for chunk in self._chunks]
            self._df = Counter()
            for tokens in self._tokens:
                self._df.update(set(tokens))
            lengths = [len(tokens) for tokens in self._tokens]
            self._avgdl = (sum(lengths) / len(lengths)) if lengths else 0.0
            self._dense_vectors = None
            self._dense_ready = False
            self._clip_vectors = None
            self._clip_ready = False
            logger.info("检索索引重建：%s 个块", len(self._chunks))

    def ensure_fresh(self, force: bool = False) -> None:
        """按数据库指纹自动重建，兼容独立解析进程。"""
        from .. import db

        try:
            fingerprint = db.chunks_fingerprint()
        except Exception as exc:  # noqa: BLE001 - 数据库短暂不可用时沿用旧索引
            logger.warning("读取索引指纹失败: %s", exc)
            return
        with self._lock:
            if not force and fingerprint == self._fingerprint:
                return
            self._fingerprint = fingerprint
        self.rebuild(db.fetch_chunks())

    # ------------------------------------------------------------------ #
    # 向量（可选）
    # ------------------------------------------------------------------ #
    @property
    def dense_enabled(self) -> bool:
        return settings.retrieval_mode == "hybrid" and bool(settings.embedding_model)

    @property
    def clip_enabled(self) -> bool:
        return bool(settings.multimodal_embedding_model) and not self._clip_failed

    def _ensure_dense(self) -> bool:
        if not self.dense_enabled or self._embedding_failed:
            return False
        with self._lock:
            if self._dense_ready:
                return True
            try:
                import numpy as np
                from sentence_transformers import SentenceTransformer

                if self._dense_model is None:
                    logger.info("加载句向量模型 %s", settings.embedding_model)
                    self._dense_model = SentenceTransformer(
                        settings.embedding_model, device=settings.embedding_device
                    )
                texts = [_chunk_text(chunk) for chunk in self._chunks] or [""]
                vectors = self._dense_model.encode(
                    texts,
                    batch_size=16,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )
                self._dense_vectors = np.asarray(vectors, dtype="float32")
                self._dense_ready = True
                return True
            except Exception as exc:  # noqa: BLE001 - 向量只是增强
                logger.warning("句向量不可用，退回 BM25：%s", exc)
                self._embedding_failed = True
                return False

    def _ensure_clip(self) -> bool:
        if not self.clip_enabled:
            return False
        with self._lock:
            if self._clip_ready:
                return True
            try:
                import numpy as np
                from PIL import Image
                from sentence_transformers import SentenceTransformer

                if self._clip_model is None:
                    logger.info("加载多模态向量模型 %s", settings.multimodal_embedding_model)
                    self._clip_model = SentenceTransformer(
                        settings.multimodal_embedding_model, device=settings.embedding_device
                    )

                figure_rows = [index for index, chunk in enumerate(self._chunks) if chunk.get("kind") == "figure"]
                if not figure_rows:
                    self._clip_ready = True
                    return True

                from pathlib import Path

                from ..config import settings as app_settings

                vectors: list[Any] = []
                kept: list[int] = []
                for row in figure_rows[:300]:
                    chunk = self._chunks[row]
                    rel = str(chunk.get("image_path") or "")
                    path = app_settings.doc_dir(str(chunk.get("doc_id") or "")) / rel
                    if not rel or not Path(path).is_file():
                        continue
                    try:
                        with Image.open(path) as image:
                            vectors.append(self._clip_model.encode(image, normalize_embeddings=True))
                        kept.append(row)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("图片编码失败 %s: %s", path, exc)

                matrix = np.asarray(vectors, dtype="float32") if vectors else np.zeros((0, 1), dtype="float32")
                self._clip_vectors = (kept, matrix)
                self._clip_ready = True
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning("多模态向量不可用，关闭图像语义检索：%s", exc)
                self._clip_failed = True
                return False

    # ------------------------------------------------------------------ #
    # 检索
    # ------------------------------------------------------------------ #
    def search(
        self,
        query: str,
        *,
        doc_ids: Sequence[str] | None = None,
        top_k: int = 6,
        kinds: Sequence[str] | None = None,
    ) -> list[Hit]:
        self.ensure_fresh()
        with self._lock:
            chunks = list(self._chunks)
            tokens_list = list(self._tokens)
            df = Counter(self._df)
            avgdl = self._avgdl
            dense_vectors = self._dense_vectors if self._dense_ready else None
            clip_pack = self._clip_vectors if self._clip_ready else None
            dense_ok = self._dense_ready
            clip_ok = self._clip_ready

        query_tokens = tokenize(query)
        if not query_tokens or not chunks:
            return []
        counts = Counter(query_tokens)
        total = len(chunks)
        k1, b = 1.4, 0.75

        candidate_rows = [
            row
            for row, chunk in enumerate(chunks)
            if (not doc_ids or str(chunk.get("doc_id")) in set(doc_ids))
            and (not kinds or str(chunk.get("kind")) in set(kinds))
        ]
        if not candidate_rows:
            return []

        bm25_scores: dict[int, float] = {}
        for row in candidate_rows:
            tokens = tokens_list[row]
            if not tokens:
                continue
            length = len(tokens)
            frequencies = Counter(tokens)
            score = 0.0
            for token, query_freq in counts.items():
                freq = frequencies.get(token, 0)
                if not freq:
                    continue
                idf = math.log(1 + (total - df.get(token, 0) + 0.5) / (df.get(token, 0) + 0.5))
                denominator = freq + k1 * (1 - b + b * (length / avgdl if avgdl else 1.0))
                score += idf * (freq * (k1 + 1) / denominator) * min(query_freq, 3)
            if score > 0:
                bm25_scores[row] = score

        # ---------------- 向量打分 ----------------
        dense_scores: dict[int, float] = {}
        if self.dense_enabled and not dense_ok:
            self._ensure_dense()
            with self._lock:
                dense_vectors = self._dense_vectors if self._dense_ready else None
                dense_ok = self._dense_ready
        if dense_ok and dense_vectors is not None and self._dense_model is not None:
            try:
                import numpy as np

                query_vector = self._dense_model.encode(
                    [query], normalize_embeddings=True, show_progress_bar=False
                )
                query_vector = np.asarray(query_vector, dtype="float32")[0]
                if getattr(dense_vectors, "shape", (0,))[0]:
                    similarities = dense_vectors @ query_vector
                    for row in candidate_rows:
                        if row < len(similarities):
                            value = float(similarities[row])
                            if value > 0.05:
                                dense_scores[row] = value
            except Exception as exc:  # noqa: BLE001
                logger.warning("向量检索失败，仅用 BM25：%s", exc)

        clip_scores: dict[int, float] = {}
        if self.clip_enabled and not clip_ok:
            self._ensure_clip()
            with self._lock:
                clip_pack = self._clip_vectors if self._clip_ready else None
                clip_ok = self._clip_ready
        if clip_ok and clip_pack is not None and self._clip_model is not None:
            try:
                import numpy as np

                kept, matrix = clip_pack
                if len(kept):
                    text_vector = self._clip_model.encode(
                        [query], normalize_embeddings=True, show_progress_bar=False
                    )
                    text_vector = np.asarray(text_vector, dtype="float32")[0]
                    similarities = matrix @ text_vector
                    allowed = set(candidate_rows)
                    for position, row in enumerate(kept):
                        if row in allowed:
                            value = float(similarities[position])
                            if value > 0.1:
                                clip_scores[row] = value
            except Exception as exc:  # noqa: BLE001
                logger.warning("图像语义检索失败：%s", exc)

        # ---------------- 融合 ----------------
        def normalize(scores: dict[int, float]) -> dict[int, float]:
            if not scores:
                return {}
            maximum = max(scores.values())
            if maximum <= 0:
                return {}
            return {key: value / maximum for key, value in scores.items()}

        bm25_norm = normalize(bm25_scores)
        dense_norm = normalize(dense_scores)
        clip_norm = normalize(clip_scores)

        weights = []
        if bm25_norm:
            weights.append((bm25_norm, 1.0))
        if dense_norm:
            weights.append((dense_norm, 0.7))
        if clip_norm:
            weights.append((clip_norm, 0.5))
        weight_total = sum(weight for _scores, weight in weights) or 1.0

        hits: list[Hit] = []
        for row in candidate_rows:
            if not any(row in scores for scores, _w in weights):
                continue
            score = sum(scores.get(row, 0.0) * weight for scores, weight in weights) / weight_total
            if score <= 0:
                continue
            chunk = chunks[row]
            matched = [token for token in counts if token in set(tokens_list[row])][:8]
            hits.append(
                Hit(
                    chunk=chunk,
                    score=score,
                    bm25=bm25_norm.get(row, 0.0),
                    dense=dense_norm.get(row, 0.0),
                    clip=clip_norm.get(row, 0.0),
                    matched=matched,
                )
            )

        hits.sort(key=lambda hit: (-hit.score, hit.chunk.get("doc_id", ""), hit.chunk.get("ord", 0)))
        return hits[: max(1, top_k)]

    # ------------------------------------------------------------------ #
    def stats(self) -> dict[str, Any]:
        with self._lock:
            kinds = Counter(str(chunk.get("kind")) for chunk in self._chunks)
            docs = {str(chunk.get("doc_id")) for chunk in self._chunks}
            return {
                "chunks": len(self._chunks),
                "documents": len(docs),
                "figures": kinds.get("figure", 0),
                "tables": kinds.get("table", 0),
                "texts": kinds.get("text", 0),
                "bm25_ready": bool(self._chunks),
                "dense_enabled": self.dense_enabled and not self._embedding_failed,
                "clip_enabled": self.clip_enabled,
                "fingerprint": self._fingerprint,
            }


index = RagIndex()
