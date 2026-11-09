"""正文提取。

抓到的 HTML 未必都能抽出正文（模板页、验证页、纯 JS 渲染页），所以这里是一条
**降级链**，每一级失败就退到下一级，最后退到搜索摘要：

    trafilatura → readability → bs4 启发式 → 搜索摘要 → snippet

返回的 ``ContentOrigin`` 记录了正文的真实来路，它既是置信度打分的输入，也是
判断「这份研究到底读没读网页」的依据 —— 降级不是隐藏失败，而是把失败显式化。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .models import ContentOrigin

logger = logging.getLogger(__name__)

# 低于这个字数就认为提取失败，继续往下降级
MIN_USEFUL_CHARS = 200

# 这些标签里的文字几乎肯定不是正文
_BOILERPLATE_TAGS = ("script", "style", "noscript", "nav", "footer", "header", "aside", "form")

_BLANK_LINES_RE = re.compile(r"\n{3,}")
_INLINE_WS_RE = re.compile(r"[ \t　]{2,}")

# HTML 里的 <meta charset="gb2312"> 声明
_META_CHARSET_RE = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([\w-]+)""", re.IGNORECASE)
# HTTP 头里显式写的 charset=
_HEADER_CHARSET_RE = re.compile(r"charset\s*=\s*([\w-]+)", re.IGNORECASE)

# 按 HTML 声明的编码试不出来时，按这个顺序兜底。
# gb18030 是 gbk/gb2312 的超集，中文站点用它可以覆盖绝大多数情况。
_FALLBACK_ENCODINGS = ("utf-8", "gb18030", "big5", "shift_jis")

# 解码失败时 Python 用 U+FFFD 占位 —— 猜错编码几乎必然产生它
REPLACEMENT_CHAR = "�"


def decode_html(raw: bytes, content_type: str = "") -> str:
    """把网页字节解码成文本。

    这里不能直接用 httpx 的 ``response.text``：大量中文站点只在 HTML 的
    ``<meta charset>`` 里声明编码，HTTP 头里不带 charset，此时 httpx 会默认按
    UTF-8 解，结果整页乱码。所以优先级是「HTTP 头显式声明 > meta 声明 > 逐个试」。

    判据是解出来不能有替换字符（U+FFFD）—— 猜错编码几乎必然产生替换字符。
    """
    if not raw:
        return ""

    candidates: list[str] = []
    header_match = _HEADER_CHARSET_RE.search(content_type or "")
    if header_match:
        candidates.append(header_match.group(1))

    meta_match = _META_CHARSET_RE.search(raw[:4096])
    if meta_match:
        candidates.append(meta_match.group(1).decode("ascii", errors="ignore"))

    candidates.extend(_FALLBACK_ENCODINGS)

    for encoding in dict.fromkeys(candidates):  # 去重且保序
        if not encoding:
            continue
        try:
            text = raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
        if REPLACEMENT_CHAR not in text:
            return text

    # 全都带替换字符，那就用第一个能解的，至少内容还在
    for encoding in dict.fromkeys(candidates):
        if not encoding:
            continue
        try:
            return raw.decode(encoding, errors="replace")
        except LookupError:
            continue
    return raw.decode("utf-8", errors="replace")


@dataclass(slots=True)
class ExtractionResult:
    text: str
    extractor: str
    origin: ContentOrigin

    @property
    def ok(self) -> bool:
        return bool(self.text)


def clean_text(text: str) -> str:
    """压掉多余空白，让正文干净且可比较。"""
    if not text:
        return ""
    # 全角空格统一成半角，避免同一段文字因空格不同而被判成两份
    text = text.replace("　", " ").replace("\xa0", " ")
    text = _INLINE_WS_RE.sub(" ", text)
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(lines)
    return _BLANK_LINES_RE.sub("\n\n", text).strip()


def _from_trafilatura(html: str, url: str) -> str:
    try:
        import trafilatura

        result = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
        )
        return clean_text(result or "")
    except Exception as exc:  # noqa: BLE001 - 第三方解析器对畸形 HTML 会抛各种异常
        logger.debug("trafilatura 提取失败 %s: %s", url, exc)
        return ""


def _from_readability(html: str, url: str) -> str:
    try:
        from bs4 import BeautifulSoup
        from readability import Document

        summary_html = Document(html).summary()
        return clean_text(BeautifulSoup(summary_html, "lxml").get_text("\n"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("readability 提取失败 %s: %s", url, exc)
        return ""


def _from_bs4(html: str, url: str) -> str:
    """最后的兜底：挑包含最多 <p> 文字的那个容器。

    对结构规范的资讯页效果尚可，对全 JS 渲染的页面基本无效 —— 那种情况只能降级。
    """
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        for tag in soup.find_all(_BOILERPLATE_TAGS):
            tag.decompose()

        best_text = ""
        for container in soup.find_all(["article", "div", "section", "main"]):
            paragraphs = container.find_all("p", recursive=False)
            if not paragraphs:
                continue
            text = clean_text("\n".join(p.get_text(" ", strip=True) for p in paragraphs))
            if len(text) > len(best_text):
                best_text = text

        if not best_text:
            best_text = clean_text(soup.get_text("\n"))
        return best_text
    except Exception as exc:  # noqa: BLE001
        logger.debug("bs4 提取失败 %s: %s", url, exc)
        return ""


def extract_content(
    html: str,
    url: str = "",
    *,
    search_summary: str = "",
    snippet: str = "",
) -> ExtractionResult:
    """按降级链提取正文，返回正文及其来路。"""
    if html:
        for name, extractor in (
            ("trafilatura", _from_trafilatura),
            ("readability", _from_readability),
            ("bs4", _from_bs4),
        ):
            text = extractor(html, url)
            if len(text) >= MIN_USEFUL_CHARS:
                return ExtractionResult(text=text, extractor=name, origin=ContentOrigin.page)
            if text:
                logger.debug("%s 只抽出 %d 字，继续降级: %s", name, len(text), url)

    # 网页这条路走不通，退到搜索接口已经给我们的内容。
    # summary 与 snippet 是两种东西：summary 是博查按全文生成的长摘要，
    # snippet 只是搜索列表里那行描述。哪怕 summary 短到进不了上面的门槛，
    # 它的来路仍然是 search_summary —— 标错来路会连带把置信度算错。
    summary = clean_text(search_summary)
    if summary:
        return ExtractionResult(
            text=summary, extractor="bocha_summary", origin=ContentOrigin.search_summary
        )

    fallback = clean_text(snippet)
    if fallback:
        return ExtractionResult(
            text=fallback, extractor="bocha_snippet", origin=ContentOrigin.snippet
        )

    return ExtractionResult(text="", extractor="none", origin=ContentOrigin.snippet)


__all__ = ["MIN_USEFUL_CHARS", "ExtractionResult", "clean_text", "extract_content"]
