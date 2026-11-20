# -*- coding: utf-8 -*-
"""MinerU (magic-pdf) PDF 解析封装。

通过 subprocess 调用 magic-pdf 命令行工具，把 PDF 解析为：
    - markdown 正文（文字 + 图片引用 ![](images/xxx.jpg)）
    - images/ 目录下的图片文件

magic-pdf 命令行用法（参考官方文档）:
    magic-pdf -p <pdf_path> -o <output_dir> -m auto

输出目录结构（magic-pdf 1.x）大致为：
    <output_dir>/<pdf_basename>/auto/<pdf_basename>.md
    <output_dir>/<pdf_basename>/auto/images/*.jpg
"""

import os
import re
import subprocess

from app.config import MAGIC_PDF_CMD, MAGIC_PDF_METHOD, PARSED_DIR


class MinerUParseError(RuntimeError):
    pass


def parse_pdf(pdf_path, doc_id):
    """调用 magic-pdf 解析 PDF，返回 (markdown_text, image_paths)。

    :param pdf_path: 原始 PDF 绝对路径
    :param doc_id:   文档 ID，用作输出子目录名，避免多文档互相覆盖
    :return: (markdown_text: str, image_abs_paths: list[str])
    """
    out_dir = os.path.join(PARSED_DIR, doc_id)
    os.makedirs(out_dir, exist_ok=True)

    cmd = [MAGIC_PDF_CMD, "-p", pdf_path, "-o", out_dir, "-m", MAGIC_PDF_METHOD]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if proc.returncode != 0:
        raise MinerUParseError(
            f"magic-pdf 解析失败 (returncode={proc.returncode}): {proc.stderr or proc.stdout}"
        )

    md_path = _find_markdown(out_dir)
    if not md_path:
        raise MinerUParseError(f"未在 {out_dir} 找到 magic-pdf 生成的 markdown 文件")

    with open(md_path, "r", encoding="utf-8") as f:
        markdown_text = f.read()

    md_dir = os.path.dirname(md_path)
    image_paths = _resolve_images(markdown_text, md_dir)
    return markdown_text, image_paths


def _find_markdown(out_dir):
    for root, _dirs, files in os.walk(out_dir):
        for name in files:
            if name.endswith(".md"):
                return os.path.join(root, name)
    return None


def _resolve_images(markdown_text, md_dir):
    """从 markdown 中提取图片引用 ![](images/xxx.jpg)，解析为绝对路径。"""
    refs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", markdown_text)
    abs_paths = []
    for ref in refs:
        ref = ref.split("?")[0]
        abs_path = os.path.normpath(os.path.join(md_dir, ref))
        if os.path.isfile(abs_path):
            abs_paths.append(abs_path)
    return abs_paths


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python -m app.mineru_parser <pdf_path>")
        sys.exit(1)
    text, imgs = parse_pdf(sys.argv[1], doc_id="debug")
    print(f"markdown 长度: {len(text)}，图片数: {len(imgs)}")
    print(text[:500])
