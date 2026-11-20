"""解析结果的数据模型。

无论用 MinerU 还是内置解析器，最终都归一化成 ``ParsedDocument``：

* ``blocks``：按阅读顺序排列的内容块（文字 / 图片 / 表格），带页码和 bbox；
* ``markdown``：便于人工核对与前端预览的 Markdown；
* ``backend``：实际生效的解析后端，写到文档表里供 UI 展示。

这样下游切块、检索、问答完全不关心 PDF 是用什么工具解析的。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

BlockKind = Literal["text", "image", "table"]


class ParseError(RuntimeError):
    """解析失败；message 会直接展示给用户。"""


@dataclass(slots=True)
class Block:
    kind: BlockKind
    page: int = 1
    bbox: tuple[float, float, float, float] | None = None
    content: str = ""
    image_path: str = ""
    caption: str = ""
    #: 0 表示正文，1/2/3 表示标题层级
    level: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    def bbox_json(self) -> str:
        if not self.bbox:
            return ""
        values: list[float] = []
        for raw in self.bbox:
            try:
                values.append(round(float(raw), 2))
            except (TypeError, ValueError):
                values.append(0.0)
        return json.dumps(values)


@dataclass(slots=True)
class ParsedDocument:
    backend: str
    page_count: int
    blocks: list[Block] = field(default_factory=list)
    markdown: str = ""
    warnings: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    @property
    def figure_count(self) -> int:
        return sum(1 for block in self.blocks if block.kind == "image")

    @property
    def text_chars(self) -> int:
        return sum(len(block.content) for block in self.blocks if block.kind == "text")

    def summary(self, limit: int = 160) -> str:
        """取正文开头做列表页摘要。"""
        for block in self.blocks:
            if block.kind == "text" and block.level == 0 and len(block.content.strip()) > 20:
                text = " ".join(block.content.split())
                return text[:limit]
        return ""

    def to_content_list(self) -> list[dict[str, Any]]:
        """导出成 MinerU ``content_list.json`` 风格，便于排查与复用。"""
        items: list[dict[str, Any]] = []
        for block in self.blocks:
            if block.kind == "text":
                item: dict[str, Any] = {
                    "type": "text",
                    "text": block.content,
                    "page_idx": max(0, block.page - 1),
                }
                if block.level:
                    item["text_level"] = block.level
            elif block.kind == "image":
                item = {
                    "type": "image",
                    "img_path": block.image_path,
                    "image_caption": [block.caption] if block.caption else [],
                    "page_idx": max(0, block.page - 1),
                }
            else:
                item = {
                    "type": "table",
                    "table_body": block.content,
                    "table_caption": [block.caption] if block.caption else [],
                    "page_idx": max(0, block.page - 1),
                }
            if block.bbox:
                item["bbox"] = list(block.bbox)
            items.append(item)
        return items

    def stats(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "page_count": self.page_count,
            "blocks": len(self.blocks),
            "figures": self.figure_count,
            "text_chars": self.text_chars,
            "warnings": self.warnings,
        }
