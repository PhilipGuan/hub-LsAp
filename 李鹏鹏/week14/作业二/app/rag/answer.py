"""答案生成：拼上下文 → 调用多模态模型 → 处理图文混排与引用。

产出约定
--------
* ``answer_md``：模型输出的 Markdown（图片标记已替换为可访问 URL）
* ``answer_html``：服务端渲染好的 HTML（前端直接插入，无需前端 Markdown 库）
* ``sources``：引用资料列表（含页码、片段、图片/页面截图地址）
* ``images``：答案里实际用到的图片

图文混排的实现方式
------------------
检索结果里每一条资料都有一个编号 ``n``，如果它带图（图片块本身，或按需附带的
页面截图），提示词允许模型在答案中写 ``![说明](IMG:n)``。后端把 ``IMG:n``
替换成真实图片地址，因此答案正文里可以自然穿插图片。
如果模型没有主动引用任何图片，系统会自动追加「相关资料图」，保证图文混排效果。
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import settings
from . import llm
from .index import Hit
from .retriever import Retrieval

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是严谨的多模态文档问答助手。你会收到从用户文档库中检索到的资料（文字、表格、图片），以及用户的问题；用户也可能随问题附上一张图片。

必须遵守：
1. 只依据【资料】回答。资料里没有的信息不要编造；确实找不到时，明确说明「资料中未提及」，并指出还缺什么。
2. 用中文回答，使用 Markdown（可用小标题、要点列表、表格），条理清晰。
3. 每条关键结论后用 [n] 标注依据的资料编号，例如 [1][3]；引用多个编号时连续书写。
4. 资料若标注了「可展示图片」，可以在回答中用 ![简短说明](IMG:n) 插入该图片，n 必须与资料编号一致；每张图最多用一次，禁止编造不存在的编号。
5. 如果用户上传了图片，请结合图片内容作答，并说明你从图中观察到的要点。
6. 尽量引用原文中的关键数据、结论和术语，避免空泛复述。
7. 不要输出与问题无关的寒暄或免责声明。"""

_IMG_PATTERN = re.compile(r"!\[([^\]]*)\]\(IMG:(\d+)\)")
_IMG_LOOSE = re.compile(r"IMG:(\d+)")
_MD_INSTANCE: Any = None


def _markdown() -> Any:
    global _MD_INSTANCE
    if _MD_INSTANCE is None:
        from markdown_it import MarkdownIt

        _MD_INSTANCE = (
            MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
            .enable("table")
            .enable("strikethrough")
        )
    return _MD_INSTANCE


def render_markdown(markdown: str) -> str:
    """Markdown → HTML（禁用原始 HTML，避免注入）。"""
    if not markdown.strip():
        return ""
    try:
        return str(_markdown().render(markdown))
    except Exception as exc:  # noqa: BLE001 - 渲染失败不能吞掉答案
        logger.warning("Markdown 渲染失败: %s", exc)
        return "<p>" + markdown.replace("\n", "<br>") + "</p>"


def _page_image_url(doc_id: str, page: int) -> tuple[str, str]:
    rel = f"pages/page-{max(1, page):04d}.png"
    path = settings.doc_dir(doc_id) / rel
    if path.is_file():
        return f"/media/{doc_id}/{rel}", str(path)
    return "", ""


@dataclass(slots=True)
class ContextItem:
    rank: int
    hit: Hit
    text: str = ""
    image_url: str = ""
    image_path: str = ""
    caption: str = ""

    @property
    def kind(self) -> str:
        return str(self.hit.chunk.get("kind") or "text")

    @property
    def page(self) -> int:
        return int(self.hit.chunk.get("page") or 1)

    def source(self) -> dict[str, Any]:
        chunk = self.hit.chunk
        page_url, _ = _page_image_url(str(chunk.get("doc_id") or ""), self.page)
        return {
            "rank": self.rank,
            "chunk_id": self.hit.id,
            "doc_id": chunk.get("doc_id", ""),
            "filename": chunk.get("filename", ""),
            "page": self.page,
            "page_end": int(chunk.get("page_end") or self.page),
            "kind": self.kind,
            "section": chunk.get("section", ""),
            "caption": chunk.get("caption", ""),
            "snippet": " ".join(str(chunk.get("content") or "").split())[:280],
            "image_url": self.image_url,
            "page_image_url": page_url,
            "score": round(self.hit.score, 4),
        }


_KIND_LABELS = {"text": "正文", "figure": "图片", "table": "表格"}


def _build_items(retrieval: Retrieval) -> list[ContextItem]:
    items: list[ContextItem] = []
    combined = [*retrieval.text_hits, *retrieval.figure_hits]
    for rank, hit in enumerate(combined, start=1):
        chunk = hit.chunk
        doc_id = str(chunk.get("doc_id") or "")
        page = int(chunk.get("page") or 1)
        kind = str(chunk.get("kind") or "text")

        image_url = ""
        image_path = ""
        caption = str(chunk.get("caption") or "")
        if kind == "figure" and chunk.get("image_url"):
            image_url = str(chunk["image_url"])
            rel = str(chunk.get("image_path") or "")
            if rel:
                candidate = settings.doc_dir(doc_id) / rel
                if candidate.is_file():
                    image_path = str(candidate)
            if not image_path:
                image_url = ""
        elif settings.rag_attach_pages:
            page_url, page_path = _page_image_url(doc_id, page)
            image_url, image_path = page_url, page_path

        items.append(
            ContextItem(
                rank=rank,
                hit=hit,
                text=" ".join(str(chunk.get("content") or "").split()),
                image_url=image_url,
                image_path=image_path,
                caption=caption or f"{_KIND_LABELS.get(kind, '内容')} · 第 {page} 页",
            )
        )
    return items


def _context_text(items: list[ContextItem], question: str) -> str:
    lines: list[str] = ["以下是从用户文档库中检索到的资料：", ""]
    used_chars = 0
    for item in items:
        chunk = item.hit.chunk
        header = (
            f"【资料{item.rank}】《{chunk.get('filename', '')}》"
            f"第 {item.page} 页 · {_KIND_LABELS.get(item.kind, item.kind)}"
        )
        section = str(chunk.get("section") or "").strip()
        if section:
            header += f" · 章节：{section}"
        body = item.text[:1600]
        block = f"{header}\n{body}"
        if item.image_url:
            block += f"\n（可展示图片：![](IMG:{item.rank})）"
        if used_chars + len(block) > settings.rag_max_context_chars:
            lines.append("（上下文已截断，仅保留最相关资料）")
            break
        lines.append(block)
        lines.append("")
        used_chars += len(block)

    lines.append(f"用户问题：{question}")
    return "\n".join(lines)


def _attach_images(
    items: list[ContextItem], question_image: str | None
) -> list[ContextItem]:
    """挑出要真正送给模型的图片：优先图片块，其次页面截图。"""
    limit = max(0, settings.rag_max_images - (1 if question_image else 0))
    if limit == 0 or not settings.rag_attach_figures:
        return []
    chosen: list[ContextItem] = []
    for item in items:
        if item.kind == "figure" and item.image_path:
            chosen.append(item)
        if len(chosen) >= limit:
            break
    return chosen


def _build_user_content(
    items: list[ContextItem],
    question: str,
    attached: list[ContextItem],
    question_image: str | None,
) -> str | list[dict[str, Any]]:
    text = _context_text(items, question)
    if not attached and not question_image:
        return text

    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for item in attached:
        try:
            data_url = llm.to_data_url(Path(item.image_path))
        except Exception as exc:  # noqa: BLE001 - 图片读不到就跳过
            logger.warning("图片读取失败 %s: %s", item.image_path, exc)
            continue
        content.append(
            {
                "type": "text",
                "text": f"【资料{item.rank} 的配图】{item.caption}",
            }
        )
        content.append({"type": "image_url", "image_url": {"url": data_url}})

    if question_image:
        content.append({"type": "image_url", "image_url": {"url": question_image}})
        content.append({"type": "text", "text": "（上面这张图是用户随问题上传的图片）"})
    content.append(
        {
            "type": "text",
            "text": "请依据上述资料回答问题，按系统要求标注 [n] 引用；需要展示图片时使用 ![](IMG:n)。",
        }
    )
    return content


def _replace_markers(
    markdown: str, items: list[ContextItem]
) -> tuple[str, list[dict[str, Any]]]:
    by_rank = {item.rank: item for item in items}
    used: list[dict[str, Any]] = []

    def repl(match: re.Match[str]) -> str:
        alt = (match.group(1) or "").strip()
        try:
            rank = int(match.group(2))
        except ValueError:
            return ""
        item = by_rank.get(rank)
        if item is None or not item.image_url:
            return f"（资料{rank}没有配图）"
        if not any(entry["rank"] == rank for entry in used):
            used.append(
                {"rank": rank, "url": item.image_url, "caption": alt or item.caption}
            )
        return f"![{alt or item.caption}]({item.image_url})"

    cleaned = _IMG_PATTERN.sub(repl, markdown)
    # 兜底：模型直接写了 IMG:3 而没有 Markdown 图片语法
    cleaned = _IMG_LOOSE.sub(lambda m: "", cleaned)
    return cleaned, used


def _mock_answer(question: str, items: list[ContextItem]) -> str:
    """离线演示：用检索结果拼一个抽取式答案，验证整条链路。"""
    lines = [f"**（离线演示模式 LLM_MOCK=1）** 关于「{question}」，检索到的资料如下：", ""]
    for item in items[:3]:
        snippet = item.text[:220].replace("[", "［")
        lines.append(f"- {snippet} [资料{item.rank}]")
    figure = next((item for item in items if item.image_url), None)
    if figure is not None:
        lines.append("")
        lines.append(f"![{figure.caption}](IMG:{figure.rank})")
        lines.append("")
        lines.append(f"*图：{figure.caption}（资料{figure.rank}）*")
    lines.append("")
    lines.append("> 在 `.env` 中填写真实模型密钥后，这里会返回模型生成的完整回答。")
    return "\n".join(lines)


def answer_question(
    question: str,
    retrieval: Retrieval,
    *,
    question_image: str | None = None,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """生成回答，返回可直接交给前端的结构。"""
    started = time.perf_counter()
    items = _build_items(retrieval)

    if not items:
        message = (
            "没有在已解析完成的文档里找到与该问题相关的内容。\n\n"
            "可以试试：\n"
            "- 确认文档状态是「解析完成」；\n"
            "- 换一种问法，带上章节名、图表名或原文关键词；\n"
            "- 上传更多相关 PDF 后再提问。"
        )
        return {
            "answer_md": message,
            "answer_html": render_markdown(message),
            "sources": [],
            "images": [],
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "retrieval": retrieval.summary(),
            "mock": settings.llm_mock,
            "warnings": list(retrieval.warnings),
        }

    if settings.llm_mock:
        markdown = _mock_answer(question, items)
    else:
        attached = _attach_images(items, question_image)
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for turn in (history or [])[-4:]:
            role = turn.get("role")
            content = str(turn.get("content_md") or "").strip()
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content[:600]})
        messages.append(
            {
                "role": "user",
                "content": _build_user_content(items, question, attached, question_image),
            }
        )
        markdown = llm.chat(messages)

    markdown, used = _replace_markers(markdown, items)

    # 模型没主动插图时，自动补一张资料图，保证「图文混排」
    if not used:
        fallback = next((item for item in items if item.image_url), None)
        if fallback is not None:
            markdown += (
                f"\n\n---\n\n**相关资料图**\n\n"
                f"![{fallback.caption}]({fallback.image_url})\n\n"
                f"*{fallback.caption}（资料{fallback.rank}）*"
            )
            used.append(
                {
                    "rank": fallback.rank,
                    "url": fallback.image_url,
                    "caption": fallback.caption,
                }
            )

    sources = [item.source() for item in items]
    return {
        "answer_md": markdown,
        "answer_html": render_markdown(markdown),
        "sources": sources,
        "images": used,
        "provider": settings.llm_provider,
        "model": settings.llm_vision_model if question_image else settings.llm_model,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "retrieval": retrieval.summary(),
        "mock": settings.llm_mock,
        "warnings": list(retrieval.warnings),
    }
