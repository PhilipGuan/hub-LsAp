"""PyMuPDF 降级解析器：不依赖任何模型的轻量解析（兜底方案）。

适用场景：MinerU 未安装 / 无 GPU / 快速联调。产出 Markdown（按页组织）+ 页面内位图。
limitations：无版面分析，表格/公式还原效果弱于 MinerU；页码信息完整。
"""
import pymupdf as fitz  # PyMuPDF 2.x 规范导入名（fitz 为旧别名）

from loguru import logger

from worker.parser.base import BaseParser, ParsedImage, ParseResult


class PyMuPDFParser(BaseParser):
    name = "pymupdf"
    MIN_IMAGE_SIZE = 32  # 过滤过小的装饰图（横线/图标）

    def __init__(self, settings):
        self.settings = settings  # 与 MinerUParser 保持一致的构造签名

    def parse(self, pdf_path: str) -> ParseResult:
        doc = fitz.open(pdf_path)
        try:
            parts: list[str] = []
            images: list[ParsedImage] = []
            seen_xrefs: set[int] = set()

            for page in doc:
                page_no = page.number  # 0 起始
                parts.append(f"\n\n### 第 {page_no + 1} 页\n\n")
                text = (page.get_text("text") or "").strip()
                if text:
                    parts.append(text + "\n")

                img_index = 0
                for info in page.get_images(full=True):
                    xref = info[0]
                    if xref in seen_xrefs:
                        continue
                    seen_xrefs.add(xref)
                    try:
                        base = doc.extract_image(xref)
                    except Exception:  # 提取失败的图跳过
                        continue
                    if base["width"] < self.MIN_IMAGE_SIZE or base["height"] < self.MIN_IMAGE_SIZE:
                        continue
                    ext = (base.get("ext") or "png").lower()
                    img_index += 1
                    ref = f"images/p{page_no + 1}_{img_index}.{ext}"
                    images.append(ParsedImage(
                        data=base["image"],
                        ext=ext,
                        source_ref=ref,
                        page_no=page_no,
                    ))
                    parts.append(f"\n![]({ref})\n\n")

            markdown = "".join(parts)
            logger.info(
                f"[pymupdf] 解析完成 file={pdf_path} images={len(images)} pages={doc.page_count}"
            )
            return ParseResult(markdown=markdown, images=images, page_count=doc.page_count)
        finally:
            doc.close()
