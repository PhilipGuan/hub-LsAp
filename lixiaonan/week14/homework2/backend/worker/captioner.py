"""图片描述生成（可选，§7 开放问题2）：调用多模态 LLM 为解析出的图片生成中文描述。

- EMBEDDING_IMAGE_CAPTION=true 且 LLM_BACKEND=openai 时启用；
  描述文本与图片周边上下文一起作为图片块的向量文本，提升图片召回率
- 未启用时图片块仅以周边上下文文本参与检索
"""
import base64

from loguru import logger

from app.core.config import Settings


def generate_caption(image_bytes: bytes, ext: str, settings: Settings) -> str | None:
    """同步调用多模态 LLM 生成一句话图片描述；失败返回 None（不阻塞解析）。"""
    if settings.llm_backend != "openai" or not settings.llm_api_key:
        return None
    try:
        from openai import OpenAI  # 同步客户端（Worker 进程为同步模型）

        client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_api_base)
        b64 = base64.b64encode(image_bytes).decode()
        resp = client.chat.completions.create(
            model=settings.llm_model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "用一句不超过 60 字的中文描述这张图片的主要内容，直接输出描述。"},
                    {"type": "image_url", "image_url": {"url": f"data:image/{ext};base64,{b64}"}},
                ],
            }],
            max_tokens=512,  # 思考模型 reasoning 会占用 token，需留足正文空间
            temperature=0.2,
            extra_body=(
                {"thinking": {"type": "enabled", "effort": settings.llm_thinking_effort}}
                if settings.llm_thinking_effort else {}
            ),
        )
        caption = (resp.choices[0].message.content or "").strip()
        logger.debug(f"图片描述生成成功: {caption[:50]}")
        return caption or None
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"图片描述生成失败（跳过，不影响解析）: {exc}")
        return None
