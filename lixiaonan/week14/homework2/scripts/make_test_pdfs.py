"""生成测试用 PDF（含中文文本与位图），用于验证解析闭环。

用法：python scripts/make_test_pdfs.py [输出目录]
依赖：pymupdf
"""
import sys
from pathlib import Path

import pymupdf


def make_image_pixmap(size: int = 120, color=(220, 60, 40)) -> pymupdf.Pixmap:
    """生成一张纯色圆形位图（作为 PDF 内嵌图片）。"""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, size, size))
    pix.clear_with(255)
    cx = cy = size // 2
    radius = size // 3
    for x in range(size):
        for y in range(size):
            if (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2:
                pix.set_pixel(x, y, color)
    return pix


def make_pdf(path: Path, pages: list[dict]) -> None:
    doc = pymupdf.open()
    for spec in pages:
        page = doc.new_page(width=595, height=842)  # A4
        y = 72
        for line in spec.get("lines", []):
            try:
                page.insert_text((72, y), line, fontname="china-s", fontsize=14)
            except Exception:
                page.insert_text((72, y), line.encode("utf-8", "ignore").decode(), fontsize=14)
            y += 28
        if spec.get("image"):
            rect = pymupdf.Rect(72, y + 20, 72 + 160, y + 180)
            page.insert_image(rect, pixmap=make_image_pixmap())
            y += 200
            page.insert_text((72, y), "上图是一个红色圆形示意图。", fontname="china-s", fontsize=12)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()
    print(f"已生成: {path}")


def main() -> None:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "data" / "test_pdfs"
    make_pdf(out_dir / "测试文档A.pdf", [
        {
            "lines": [
                "多模态 RAG 问答系统测试文档 A",
                "第一页：本系统基于 Kafka 解耦文档上传与解析流程。",
                "MinerU 负责 PDF 解析，产出 Markdown 与图片。",
                "解析状态通过数据库维护，前端轮询展示。",
            ],
            "image": False,
        },
        {
            "lines": [
                "第二页：本页包含一张图片。",
            ],
            "image": True,
        },
    ])
    make_pdf(out_dir / "测试文档B.pdf", [
        {
            "lines": [
                "测试文档 B：单页文档。",
                "用于验证批量上传与并发解析。",
            ],
            "image": False,
        },
    ])


if __name__ == "__main__":
    main()
