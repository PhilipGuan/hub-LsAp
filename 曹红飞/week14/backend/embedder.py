"""嵌入模型单例：BGE-M3（文本，1024 维）与 CLIP ViT-B-32（图像，512 维）。"""
import threading
from typing import List

import numpy as np
from PIL import Image
from sentence_transformers import SentenceTransformer

TEXT_DIM = 1024
IMAGE_DIM = 512

_text_model: SentenceTransformer | None = None
_image_model: SentenceTransformer | None = None
_lock = threading.Lock()


def load_models() -> None:
    """懒加载两个嵌入模型；首次运行会从 HuggingFace 下载，耗时较长。"""
    global _text_model, _image_model
    with _lock:
        if _text_model is None:
            _text_model = SentenceTransformer("BAAI/bge-m3")
        if _image_model is None:
            _image_model = SentenceTransformer("clip-ViT-B-32")


def start_warmup() -> None:
    """后台线程预热模型，避免首个请求阻塞过久。"""
    threading.Thread(target=load_models, daemon=True, name="embedder-warmup").start()


def embed_text(texts: List[str]) -> np.ndarray:
    load_models()
    return _text_model.encode(texts, normalize_embeddings=True)


def embed_image_paths(image_paths: List[str]) -> np.ndarray:
    load_models()
    images = [Image.open(p).convert("RGB") for p in image_paths]
    return _image_model.encode(images, normalize_embeddings=True)


def embed_pil_images(images: List[Image.Image]) -> np.ndarray:
    load_models()
    return _image_model.encode(images, normalize_embeddings=True)
