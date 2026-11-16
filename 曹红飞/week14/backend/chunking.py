"""把 MinerU 解析出的 markdown 切成带图片引用的分块。"""
import re
from pathlib import Path
from typing import Dict, List

from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)


def load_and_chunk(
    md_path: str | Path,
    images_dir: str | Path,
    chunk_size: int = 900,
    chunk_overlap: int = 150,
) -> List[Dict]:
    """按标题结构切分 markdown，并解析出每个分块引用的图片绝对路径。

    返回 [{"text": str, "images": [绝对图片路径, ...]}]
    """
    md_path = Path(md_path)
    images_dir = Path(images_dir)

    text = md_path.read_text(encoding="utf-8")

    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#", "h1"), ("##", "h2"), ("###", "h3")]
    )
    header_splits = header_splitter.split_text(text)

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", ".", "!", "?", " ", ""],
    )

    chunks: List[Dict] = []
    for doc in header_splits:
        for split in text_splitter.split_text(doc.page_content):
            img_refs = re.findall(r"!\[.*?\]\((.*?)\)", split)
            resolved_images = []
            for ref in img_refs:
                full_path = images_dir / Path(ref).name
                if full_path.exists():
                    resolved_images.append(str(full_path))
            chunks.append({"text": split.strip(), "images": resolved_images})
    return chunks
