"""
向量编码：文本用 bge，文本/图片用 CLIP（jina-clip-v2）。

模型采用惰性加载：只有真正需要编码时才下载/加载，前端启动速度不受影响。
"""
import logging
import os
import traceback
from typing import List

from config import (
    BGE_DIM,
    BGE_MODEL,
    CLIP_DIM,
    CLIP_MODEL,
    CLIP_TRUNCATE_DIM,
    EMBEDDING_DEVICE,
    HF_ENDPOINT,
)
from text_utils import clean_text_for_embedding, find_local_images

os.environ.setdefault("HF_ENDPOINT", HF_ENDPOINT)

logger = logging.getLogger(__name__)

_bge_model = None
_clip_model = None


def _device() -> str:
    if EMBEDDING_DEVICE:
        return EMBEDDING_DEVICE
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def get_bge_model():
    global _bge_model
    if _bge_model is None:
        from sentence_transformers import SentenceTransformer

        logger.info("加载文本向量模型 %s ...", BGE_MODEL)
        _bge_model = SentenceTransformer(BGE_MODEL, device=_device())
        logger.info("文本向量模型加载完成")
    return _bge_model


def get_clip_model():
    global _clip_model
    if _clip_model is None:
        from sentence_transformers import SentenceTransformer

        logger.info("加载多模态向量模型 %s ...", CLIP_MODEL)
        _clip_model = SentenceTransformer(
            CLIP_MODEL,
            trust_remote_code=True,
            truncate_dim=CLIP_TRUNCATE_DIM,
            device=_device(),
        )
        logger.info("多模态向量模型加载完成")
    return _clip_model


def _zeros(dim: int) -> List[float]:
    return [0.0] * dim


def encode_text(text: str) -> List[float]:
    """bge 文本向量（用于文本检索）。"""
    text = (text or "").strip()[:2000]
    if not text:
        return _zeros(BGE_DIM)
    try:
        vector = get_bge_model().encode(text, normalize_embeddings=True)
        return vector.tolist()
    except Exception:
        traceback.print_exc()
        return _zeros(BGE_DIM)


def encode_clip_text(text: str) -> List[float]:
    """CLIP 文本向量（用于“文本查图片”）。"""
    text = (text or "").strip()[:2000]
    if not text:
        return _zeros(CLIP_DIM)
    try:
        vector = get_clip_model().encode(text, normalize_embeddings=True)
        return vector.tolist()
    except Exception:
        traceback.print_exc()
        return _zeros(CLIP_DIM)


def encode_clip_image(image_path) -> List[float]:
    """CLIP 图片向量（用于“图片查图片”）。"""
    if not image_path:
        return _zeros(CLIP_DIM)
    try:
        from PIL import Image

        with Image.open(image_path) as image:
            vector = get_clip_model().encode(
                image.convert("RGB"), normalize_embeddings=True
            )
        return vector.tolist()
    except Exception:
        traceback.print_exc()
        return _zeros(CLIP_DIM)


class EncodedChunk:
    """一个 chunk 的三路向量 + 是否带图片。"""

    __slots__ = ("text_vector", "clip_text_vector", "clip_image_vector", "has_image")

    def __init__(
        self,
        text_vector: List[float],
        clip_text_vector: List[float],
        clip_image_vector: List[float],
        has_image: bool,
    ):
        self.text_vector = text_vector
        self.clip_text_vector = clip_text_vector
        self.clip_image_vector = clip_image_vector
        self.has_image = has_image


def encode_chunk(text: str, image_dir) -> EncodedChunk:
    """
    对一个 markdown chunk 编码：
    - text_vector:        bge 文本向量
    - clip_text_vector:   CLIP 文本向量（用于文本检索图片）
    - clip_image_vector:  chunk 中第一张图片的 CLIP 向量；没有图片则为零向量
    """
    clean_text = clean_text_for_embedding(text)

    text_vector = encode_text(clean_text)
    clip_text_vector = encode_clip_text(clean_text) if clean_text else _zeros(CLIP_DIM)

    clip_image_vector = _zeros(CLIP_DIM)
    has_image = False
    local_images = find_local_images(text, image_dir, limit=1)
    if local_images:
        clip_image_vector = encode_clip_image(local_images[0])
        has_image = True

    return EncodedChunk(text_vector, clip_text_vector, clip_image_vector, has_image)
