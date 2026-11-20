"""文本向量化：智谱 embedding-3；无 Key 时用字符 n-gram 哈希向量做可用的离线降级。"""

import hashlib
import logging
import math
from functools import lru_cache

from ..config import get_settings

log = logging.getLogger(__name__)


def _hash_vector(text: str, dim: int) -> list[float]:
    vec = [0.0] * dim
    for i in range(len(text) - 1):
        gram = text[i : i + 2]
        h = int.from_bytes(hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest(), "big")
        vec[h % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


@lru_cache(maxsize=1)
def _client():
    from zhipuai import ZhipuAI

    settings = get_settings()
    return ZhipuAI(api_key=settings.zhipu_api_key)


def embed_texts(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    if not texts:
        return []
    if settings.use_mock_embedding:
        return [_hash_vector(t, settings.embed_dim) for t in texts]

    vectors: list[list[float]] = []
    for start in range(0, len(texts), 32):
        batch = [t[:6000] if t.strip() else "空" for t in texts[start : start + 32]]
        resp = _client().embeddings.create(
            model=settings.embed_model,
            input=batch,
            dimensions=settings.embed_dim,
        )
        ordered = sorted(resp.data, key=lambda d: d.index)
        vectors.extend([list(d.embedding) for d in ordered])
    return vectors


def embed_query(text: str) -> list[float]:
    return embed_texts([text or "空"])[0]
