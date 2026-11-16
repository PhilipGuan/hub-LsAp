"""Chinese-friendly tokenization and small text-normalisation helpers."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Iterable

import jieba


STOP_WORDS = {
    "的", "了", "和", "与", "及", "或", "是", "在", "有", "为", "对", "从", "到",
    "把", "被", "让", "给", "而", "也", "都", "就", "又", "还", "并", "很", "太",
    "吗", "呢", "吧", "啊", "呀", "哦", "嘛", "么",
    "什么", "哪些", "哪个", "怎么", "如何", "为什么", "是否", "能否", "可否",
    "请问", "请", "一下", "介绍", "简述", "总结", "说明", "文档", "内容", "资料",
    "可以", "分别", "主要", "相关", "关于", "一个", "一种", "以及", "通过", "进行",
    "这个", "那个", "这些", "那些", "里面", "其中", "然后", "如果", "因为", "所以",
    "我们", "你们", "他们", "它", "我", "你", "他", "她", "其", "该",
}

_PUNCT_RE = re.compile(r"^[\W_]+$", re.UNICODE)
_WS_RE = re.compile(r"[ \t　]+")
_BLANK_RE = re.compile(r"\n{3,}")
_TAG_RE = re.compile(r"<[^>]+>")
_SENTENCE_RE = re.compile(r"(?<=[。！？!?；;：:\n])")


def tokenize(text: str) -> list[str]:
    """Tokenize mixed Chinese/English text into terms suitable for FTS5."""
    if not text:
        return []
    normalized = text.lower().replace(" ", " ")
    result: list[str] = []
    seen: set[str] = set()
    for raw in jieba.cut_for_search(normalized):
        token = raw.strip()
        if not token or token in STOP_WORDS or _PUNCT_RE.match(token):
            continue
        if token.isascii() and len(token) < 2:
            continue
        if token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def search_expression(tokens: Iterable[str]) -> str:
    """Build a safe FTS5 OR expression from already-sanitized tokens."""
    safe = []
    for token in tokens:
        cleaned = re.sub(r'["*():^\-]', " ", token).strip()
        if cleaned:
            safe.append(f'"{cleaned}"')
    return " OR ".join(safe)


def normalize_ws(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS_RE.sub(" ", text)
    text = _BLANK_RE.sub("\n\n", text)
    return text.strip()


def strip_html(text: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    value = re.sub(r"</(p|div|li|tr|h\d)>", "\n", value, flags=re.I)
    value = re.sub(r"</(td|th)>", " | ", value, flags=re.I)
    value = _TAG_RE.sub("", value)
    return normalize_ws(html.unescape(value))


class _TableTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._cell is not None:
            if self._row is not None:
                cell = normalize_ws("".join(self._cell)).replace("\n", " ")
                self._row.append(cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(cell.strip() for cell in self._row):
                self.rows.append(self._row)
            self._row = None


def html_table_to_text(value: str) -> str:
    """Turn MinerU's HTML table into compact pipe-separated rows."""
    if not value:
        return ""
    if "<" not in value:
        return normalize_ws(value)
    parser = _TableTextParser()
    try:
        parser.feed(value)
    except Exception:
        return strip_html(value)
    if not parser.rows:
        return strip_html(value)
    lines: list[str] = []
    for row in parser.rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def split_text(text: str, max_chars: int = 850) -> list[str]:
    """Split long block text on paragraph/sentence boundaries."""
    text = normalize_ws(text)
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    pieces: list[str] = []
    for paragraph in text.split("\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= max_chars:
            pieces.append(paragraph)
            continue
        buffer = ""
        for sentence in _SENTENCE_RE.split(paragraph):
            if not sentence:
                continue
            if len(buffer) + len(sentence) <= max_chars:
                buffer += sentence
            else:
                if buffer.strip():
                    pieces.append(buffer.strip())
                buffer = sentence
                while len(buffer) > max_chars:
                    pieces.append(buffer[:max_chars].strip())
                    buffer = buffer[max_chars:]
        if buffer.strip():
            pieces.append(buffer.strip())

    merged: list[str] = []
    current = ""
    for piece in pieces:
        if not current:
            current = piece
        elif len(current) + 1 + len(piece) <= max_chars:
            current += "\n" + piece
        else:
            merged.append(current)
            current = piece
    if current:
        merged.append(current)
    return merged


def snippet(text: str, limit: int = 240) -> str:
    clean = normalize_ws(strip_html(text)).replace("\n", " ")
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


def is_visual_query(text: str) -> bool:
    lowered = text.lower()
    keywords = (
        "图", "图片", "图里", "图中", "截图", "照片", "示意图", "架构", "流程",
        "图表", "曲线", "表格", "画", "视觉", "长什么样", "界面", "对比图",
        "image", "figure", "diagram", "chart", "screenshot",
    )
    return any(keyword in lowered for keyword in keywords)


def image_placeholder(caption: str | None) -> str:
    if caption and caption.strip():
        return caption.strip()
    return "文档中的一张插图"
