# -*- coding: utf-8 -*-
"""bge / CLIP 模型懒加载与编码。"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

from ..core.config import BGE_MODEL_PATH, CLIP_MODEL_PATH, EMBED_DIM

_lock = threading.Lock()
_models: dict[str, object] = {}


def get_models():
    if _models:
        return _models["bge"], _models["clip"]

    with _lock:
        if _models:
            return _models["bge"], _models["clip"]

        import torch
        from sentence_transformers import SentenceTransformer

        device = "cuda" if torch.cuda.is_available() else "cpu"
        for name, path in (("bge", BGE_MODEL_PATH), ("clip", CLIP_MODEL_PATH)):
            if not Path(path, "config.json").exists():
                raise RuntimeError(f"本地模型缺失：{path}。请把模型拷贝到 backend/models/ 下。")

        _models["bge"] = SentenceTransformer(BGE_MODEL_PATH, device=device)
        _models["clip"] = SentenceTransformer(
            CLIP_MODEL_PATH,
            device=device,
            trust_remote_code=True,
            truncate_dim=EMBED_DIM,
        )
    return _models["bge"], _models["clip"]


def _to_list(vector) -> list[float]:
    return [float(x) for x in np.asarray(vector).reshape(-1)]


def encode_text(text: str) -> list[float]:
    bge, _ = get_models()
    return _to_list(bge.encode(text or "", normalize_embeddings=True))


def encode_clip_text(text: str) -> list[float]:
    _, clip = get_models()
    return _to_list(clip.encode(text or "", normalize_embeddings=True))


def encode_image(path: str | Path) -> list[float]:
    _, clip = get_models()
    return _to_list(clip.encode(str(path), normalize_embeddings=True))


def zero_vector() -> list[float]:
    return [0.0] * EMBED_DIM
