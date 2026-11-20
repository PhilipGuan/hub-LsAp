import base64
import html
import json
import logging
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Optional, Tuple

import httpx


@dataclass(frozen=True)
class Block:
    page: int
    text: str
    image: Optional[Tuple[str, bytes]] = None


class ParseError(Exception):
    pass


def image_type(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def get_text(item):
    fields = ("text", "image_caption", "image_footnote", "table_caption", "table_footnote", "table_body")
    parts = []
    for field in fields:
        value = item.get(field)
        if isinstance(value, str):
            parts.append(value)
        if isinstance(value, list):
            parts.extend(part for part in value if isinstance(part, str))
    return html.unescape(re.sub(r"<[^>]+>", " ", "\n".join(parts))).strip()


def extract(result):
    content = result["content_list"]
    items = json.loads(content) if isinstance(content, str) else content
    if not isinstance(items, list):
        raise ParseError("MinerU 未返回有效的 content_list。")
    images = result.get("images", {})
    blocks = []
    for index, item in enumerate(items):
        page_idx = item["page_idx"]
        text = get_text(item)
        image_path = item.get("img_path")
        if image_path:
            name = PurePosixPath(image_path.replace("\\", "/")).name
            encoded = images.get(image_path) or images.get(name)
            if not encoded:
                raise ParseError("MinerU 返回的内容缺少引用图片。")
            data = base64.b64decode(encoded.split(",", 1)[-1], validate=True)
            mime = image_type(data)
            if not mime:
                raise ParseError("MinerU 返回了不支持的图片格式。")
            neighbors = []
            for nearby in items[max(0, index - 2):index + 3]:
                if nearby is not item and nearby.get("page_idx") == page_idx:
                    neighbors.append(get_text(nearby)[:250])
            context = "\n".join(part for part in [text, *neighbors] if part)
            blocks.append(Block(page_idx + 1, context[:1600] or f"第 {page_idx + 1} 页图片", (mime, data)))
        elif text:
            for start in range(0, len(text), 780):
                blocks.append(Block(page_idx + 1, text[start:start + 850]))
    if not blocks:
        raise ParseError("PDF 中没有可入库的文本或图片。")
    return blocks


class MinerU:
    def __init__(self, url, backend="pipeline", language="ch", timeout=600, client=None):
        self.url = url
        self.backend = backend
        self.language = language
        self.client = client if client is not None else httpx.Client(timeout=timeout)

    def parse(self, pdf):
        try:
            response = self.client.post(self.url, files={"files": ("document.pdf", pdf, "application/pdf")}, data={
                "backend": self.backend, "lang_list": self.language, "parse_method": "auto",
                "return_md": "false", "return_content_list": "true", "return_images": "true",
                "response_format_zip": "false",
            })
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ParseError(f"MinerU 解析请求失败（HTTP {exc.response.status_code}）。") from exc
        except httpx.RequestError as exc:
            raise ParseError("MinerU 连接异常或解析超时。") from exc
        try:
            return extract(response.json()["results"]["document"])
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ParseError("MinerU 返回格式不匹配，请检查 /file_parse 接口版本。") from exc


def process_job(store, parse, doc_id, name, pdf):
    if not store.begin(doc_id, name):
        return
    try:
        blocks = parse(pdf)
    except ParseError as exc:
        logging.warning("Parse failed for %s: %s", doc_id, exc)
        store.fail(doc_id, str(exc))
        return
    store.finish(doc_id, blocks)
