"""多模态问答：向量检索 + 图文调用大模型。"""
import base64
import io
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

from openai import OpenAI
from PIL import Image

from . import config, milvus_store
from .embedder import embed_pil_images, embed_text

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "你是文档问答助手。请根据提供的资料片段和图片回答用户问题；"
    "如果资料不足以回答，请如实说明。回答中可以用“图N”引用附带的图片。"
)

_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
    return _client


def _clean_text(text: str) -> str:
    """去掉 markdown 图片语法，避免干扰大模型。"""
    return re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text).strip()


def _image_mime(path: Path) -> str:
    return "image/png" if path.suffix.lower() == ".png" else "image/jpeg"


def _to_b64(path: Path) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _retrieve(question: str, query_image: Optional[Image.Image]) -> List[Dict]:
    if query_image is not None:
        vec = embed_pil_images([query_image])[0].tolist()
        return milvus_store.search_image(vec, config.TOP_K)
    vec = embed_text([question])[0].tolist()
    return milvus_store.search_text(vec, config.TOP_K)


def ask(question: str, query_image: Optional[Image.Image] = None) -> Dict:
    """检索并调用大模型，返回 {"answer": str, "images": [{"url", "caption"}]}。"""
    hits = _retrieve(question, query_image)
    if not hits:
        return {"answer": "知识库中没有检索到相关内容，请先上传并解析文档。", "images": []}

    # 资料片段文本
    parts: List[Dict] = []
    context_lines = ["以下是从文档中检索到的资料片段："]
    for i, hit in enumerate(hits, 1):
        context_lines.append(f"\n[片段{i}]\n{_clean_text(hit['text'])}")
    parts.append({"type": "text", "text": "\n".join(context_lines)})

    # 检索片段附带的图片（按图1、图2编号，大模型可引用）
    images: List[Dict] = []
    seen = set()
    for hit in hits:
        rel = hit.get("image_path") or ""
        if not rel or rel in seen:
            continue
        seen.add(rel)
        img_file = config.PARSED_DIR / rel
        if not img_file.is_file():
            continue
        idx = len(images) + 1
        parts.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:{_image_mime(img_file)};base64,{_to_b64(img_file)}"
            },
        })
        parts.append({"type": "text", "text": f"（图{idx}）"})
        images.append({"url": f"/api/docs/{rel}", "caption": f"图{idx}"})

    # 用户上传的图片（如有）
    if query_image is not None:
        buf = io.BytesIO()
        query_image.save(buf, format="JPEG")
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })
        parts.append({"type": "text", "text": "（用户上传的图片）"})

    parts.append({"type": "text", "text": f"问题：{question}\n请根据上述资料回答。"})

    response = _get_client().chat.completions.create(
        model=config.LLM_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": parts},
        ],
    )
    answer = response.choices[0].message.content or ""
    return {"answer": answer, "images": images}
