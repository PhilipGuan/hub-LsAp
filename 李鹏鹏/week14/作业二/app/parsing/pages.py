"""页面渲染：把 PDF 每页转成 PNG。

用途有两个：
1. 图问检索命中正文段落时，可以把「所在页面截图」一并送给多模态模型，
   让回答真正做到图文混排；
2. 前端引用详情里可以直接看原页面。

pypdfium2 是纯 wheel 依赖，不需要系统装 Poppler；缺失时自动降级为不生成页面图。
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def render_pages(
    pdf_path: Path,
    out_dir: Path,
    *,
    dpi: int = 120,
    keep_ratio: float = 1.0,
) -> list[tuple[Path, int, int]]:
    """渲染 PDF 所有页面，返回 ``[(png_path, width, height), ...]``。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        import pypdfium2 as pdfium
    except ModuleNotFoundError:  # pragma: no cover - 可选依赖
        logger.warning("未安装 pypdfium2，跳过页面渲染")
        return []

    results: list[tuple[Path, int, int]] = []
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        scale = max(1, round(dpi / 72.0 * max(0.2, keep_ratio)))
        for index in range(len(pdf)):
            page = pdf[index]
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil()
            target = out_dir / f"page-{index + 1:04d}.png"
            image.save(target, format="PNG", optimize=True)
            results.append((target, image.width, image.height))
            bitmap.close()
            page.close()
    finally:
        pdf.close()
    return results


def render_page_png(pdf_path: Path, page_number: int, target: Path, dpi: int = 170) -> bool:
    """按需渲染单页（用于补图），失败返回 False。"""
    try:
        import pypdfium2 as pdfium
    except ModuleNotFoundError:  # pragma: no cover
        return False
    try:
        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            if not 1 <= page_number <= len(pdf):
                return False
            page = pdf[page_number - 1]
            bitmap = page.render(scale=max(1, round(dpi / 72.0)))
            bitmap.to_pil().save(target, format="PNG", optimize=True)
            bitmap.close()
            page.close()
            return True
        finally:
            pdf.close()
    except Exception as exc:  # noqa: BLE001 - 补图失败不影响主流程
        logger.warning("渲染第 %s 页失败: %s", page_number, exc)
        return False
