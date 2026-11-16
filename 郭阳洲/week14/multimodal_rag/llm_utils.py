import os
from openai import OpenAI
from sentence_transformers import SentenceTransformer
import config

# 国内下载模型走 hf-mirror 镜像，避免 huggingface 访问慢/失败
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

_vlm = None
_embed_model = None


def get_vlm_client():
    global _vlm
    if _vlm is None:
        _vlm = OpenAI(base_url=config.VLM_BASE_URL, api_key=config.VLM_API_KEY)
    return _vlm


def get_embed_model():
    global _embed_model
    if _embed_model is None:
        _embed_model = SentenceTransformer(config.EMBED_MODEL_NAME)
    return _embed_model


def embed_texts(texts):
    """批量把文本转成向量，返回 list[list[float]]"""
    model = get_embed_model()
    return model.encode(list(texts), normalize_embeddings=True).tolist()


def ask_multimodal(prompt, image_data_urls=None):
    """文本 + 可选图片一起送给多模态 LLM，返回回答文本"""
    client = get_vlm_client()
    content = [{"type": "text", "text": prompt}]
    for url in (image_data_urls or []):
        content.append({"type": "image_url", "image_url": {"url": url}})

    resp = client.chat.completions.create(
        model=config.VLM_MODEL,
        messages=[{"role": "user", "content": content}],
        temperature=0.7,
        max_tokens=1024,
    )
    return resp.choices[0].message.content