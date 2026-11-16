"""多模态问答：智谱 GLM-4V 流式输出 + 检索增强 + 图文混排。"""

import base64
import logging
import re
import time
from collections.abc import Iterator
from pathlib import Path

from ..config import get_settings
from .embedder import embed_query
from .es_store import search

log = logging.getLogger(__name__)

SAFE_IMAGE_RE = re.compile(r"^/static/(parsed|chat)/[A-Za-z0-9_.\-/]+$")

SYSTEM_PROMPT = """你是"文档助手"，基于给定的文档片段回答用户问题。

规则：
1. 只依据【参考片段】回答；片段中没有的信息要明确说"文档中未找到相关说明"。
2. 使用中文，Markdown 排版，可用列表、表格、加粗。
3. 如果片段里带有可用图片，且图片能帮助说明答案，用 Markdown 图片语法引用，路径必须来自"可用图片"列表，不得编造。
4. 回答末尾用一行列出引用来源，格式：来源：[文件名] 第N页。
"""


def retrieve(question: str, top_k: int | None = None) -> list[dict]:
    settings = get_settings()
    if not question.strip():
        return []
    try:
        vector = embed_query(question)
        return search(question, vector, top_k=top_k or settings.top_k)
    except Exception:
        log.exception("retrieval failed")
        return []


def _format_sources(sources: list[dict]) -> str:
    if not sources:
        return "（没有检索到相关片段）"
    blocks = []
    for i, s in enumerate(sources, 1):
        imgs = "\n".join(f"- {u}" for u in s.get("images", [])[:4])
        block = (
            f"[{i}] 文件：{s.get('filename')} 第{s.get('page')}页"
            f"{(' 章节：' + s['heading']) if s.get('heading') else ''}\n"
            f"{s.get('content', '')[:1200]}"
        )
        if imgs:
            block += f"\n可用图片：\n{imgs}"
        blocks.append(block)
    return "\n\n".join(blocks)


def build_messages(
    question: str,
    sources: list[dict],
    image_b64_list: list[str],
    history: list[dict] | None = None,
) -> list[dict]:
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "system",
            "content": "【参考片段】\n" + _format_sources(sources),
        },
    ]
    for item in (history or [])[-6:]:
        if item.get("role") in ("user", "assistant") and item.get("content"):
            messages.append({"role": item["role"], "content": item["content"][:1500]})

    content: list[dict] = [{"type": "text", "text": question or "请描述这张图片。"}]
    for b64 in image_b64_list:
        content.append({"type": "image_url", "image_url": {"url": b64}})
    messages.append({"role": "user", "content": content})
    return messages


def _mock_stream(question: str, sources: list[dict]) -> Iterator[str]:
    yield "（离线模拟回答：当前 `MOCK_LLM=true`，未调用真实模型）\n\n"
    if not sources:
        yield "没有检索到相关文档片段。请先上传并解析 PDF，或检查检索服务。"
        return
    yield f"根据《{sources[0].get('filename')}》第 {sources[0].get('page')} 页的内容：\n\n"
    for i, s in enumerate(sources[:3], 1):
        yield f"{i}. {s.get('content', '')[:160]}…\n"
        for url in s.get("images", [])[:1]:
            if SAFE_IMAGE_RE.match(url):
                yield f"\n![文档图片 {i}]({url})\n"
    yield "\n\n来源：" + "；".join(
        f"{s.get('filename')} 第{s.get('page')}页" for s in sources[:3]
    )


def _open_stream(client, model: str, messages: list[dict]):
    return client.chat.completions.create(
        model=model,
        messages=messages,
        stream=True,
        temperature=0.3,
    )


def stream_answer(
    question: str,
    sources: list[dict],
    image_b64_list: list[str],
    history: list[dict] | None = None,
) -> Iterator[str]:
    settings = get_settings()
    if settings.use_mock_llm:
        yield from _mock_stream(question, sources)
        return

    from zhipuai import ZhipuAI

    client = ZhipuAI(api_key=settings.zhipu_api_key)
    messages = build_messages(question, sources, image_b64_list, history)

    # 免费多模态模型高峰期限流（429 / 1305）：重试一次，再不行切备用模型
    candidates = [settings.llm_model]
    if settings.llm_fallback_model and settings.llm_fallback_model != settings.llm_model:
        candidates.append(settings.llm_fallback_model)

    stream = None
    last_error: Exception | None = None
    for model in candidates:
        for attempt in (1, 2):
            try:
                stream = _open_stream(client, model, messages)
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                log.warning("llm %s attempt %s failed: %s", model, attempt, exc)
                time.sleep(attempt)
        if stream is not None:
            if model != settings.llm_model:
                log.info("llm fallback to %s", model)
            break

    if stream is None:
        yield f"\n\n[模型调用失败] {last_error}"
        return

    try:
        for chunk in stream:
            try:
                delta = chunk.choices[0].delta.content
            except (AttributeError, IndexError):
                continue
            if delta:
                yield delta
    except Exception as exc:  # noqa: BLE001
        log.exception("llm stream failed")
        yield f"\n\n[模型调用失败] {exc}"


def sanitize_images(text: str) -> str:
    """防止模型输出非白名单图片路径。"""

    def _repl(m: re.Match) -> str:
        url = m.group(2)
        return m.group(0) if SAFE_IMAGE_RE.match(url) else f"[图片已屏蔽：{m.group(1)}]"

    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", _repl, text)


def image_to_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")
