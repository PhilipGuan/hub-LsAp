"""内置（兜底）PDF 解析器：pypdfium2 渲染页面 + pdfplumber 抽取文字/表格/图片。

为什么需要它
------------
MinerU 能给出更好的版面理解，但安装体积大、首次运行要下载模型。
本解析器只依赖轻量 wheel，保证「克隆即能跑」，并且输出与 MinerU 完全同构的
``content_list``，所以检索/问答链路无需感知后端差异：

* 每页渲染成 PNG（页面图 → 视觉问答与引用预览）；
* 用字号/行距启发式识别标题与段落，输出带 bbox 的文字块；
* 抽取页面内嵌图片并按页码裁切，配合邻近文字猜测图注；
* 表格用 pdfplumber 还原成 Markdown。

它不是 MinerU 的替代品（复杂双栏/公式/扫描件效果有限），但足以让整条
多模态 RAG 链路在无 GPU、无模型下载的环境里跑通。
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path

from ..config import settings
from .base import Block, ParsedDocument, ParseError

logger = logging.getLogger(__name__)

_CAPTION_RE = re.compile(
    r"^\s*(图|圖|表|Figure|Fig\.?|Table|Chart)\s*[\d一二三四五六七八九十]+", re.IGNORECASE
)
_PAGE_NUMBER_RE = re.compile(r"^\s*[-–—]?\s*\d{1,4}\s*[-–—]?\s*$")
_BULLET = re.compile(r"^\s*([•·▪◦*\-–—]|\(?\d+[.)、]|[一二三四五六七八九十]+[、.)])")
_PROGRESS = Callable[[float, str], None]


def _noop_progress(_value: float, _stage: str) -> None:
    return


def _median(values: list[float], default: float) -> float:
    if not values:
        return default
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _table_to_markdown(rows: list[list[str | None]]) -> str:
    cleaned = [
        [(cell or "").replace("\n", " ").replace("|", "\\|").strip() for cell in row]
        for row in rows
        if any((cell or "").strip() for cell in row)
    ]
    if not cleaned:
        return ""
    width = max(len(row) for row in cleaned)
    cleaned = [row + [""] * (width - len(row)) for row in cleaned]
    header = cleaned[0]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * width) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in cleaned[1:])
    return "\n".join(lines)


def _crop_figure(page_image, box: tuple[float, float, float, float], target: Path) -> tuple[int, int]:
    left, top, right, bottom = box
    left = max(0, int(left))
    top = max(0, int(top))
    right = min(page_image.width, int(right))
    bottom = min(page_image.height, int(bottom))
    if right - left < 40 or bottom - top < 40:
        return 0, 0
    crop = page_image.crop((left, top, right, bottom))
    crop.save(target, format="PNG", optimize=True)
    return crop.width, crop.height


def parse(
    pdf_path: Path,
    doc_dir: Path,
    progress: _PROGRESS | None = None,
) -> ParsedDocument:
    """解析 PDF；失败抛 ``ParseError``。"""
    report = progress or _noop_progress
    try:
        import pdfplumber
        import pypdfium2 as pdfium
    except ModuleNotFoundError as exc:  # pragma: no cover - 依赖缺失
        raise ParseError(
            f"内置解析器依赖缺失（{exc.name}），请先执行 pip install -r requirements.txt"
        ) from exc

    report(0.08, "渲染页面")
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        page_count = len(document)
    finally:
        document.close()
    if page_count <= 0:
        raise ParseError("PDF 没有可用页面")

    images_dir = doc_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    # 页面图与图片裁切共用一次高分辨率渲染，避免重复渲染拖慢速度
    render_dpi = max(settings.page_image_dpi, settings.figure_image_dpi)
    scale = max(1, round(render_dpi / 72.0))
    page_target_dpi = max(60, settings.page_image_dpi)

    parsed = ParsedDocument(backend="lite", page_count=page_count)
    figure_seq = 0
    heading_sizes: list[float] = []
    body_sizes: list[float] = []
    page_payloads: list[dict] = []

    document = pdfium.PdfDocument(str(pdf_path))
    try:
        for index in range(page_count):
            bitmap = None
            try:
                page = document[index]
                bitmap = page.render(scale=scale)
                page_image = bitmap.to_pil()
                page.close()

                # 存一张较小的页面图（用于前端引用预览）
                ratio = page_target_dpi / render_dpi
                if ratio < 0.99:
                    preview = page_image.resize(
                        (max(1, int(page_image.width * ratio)), max(1, int(page_image.height * ratio)))
                    )
                else:
                    preview = page_image
                preview.save(
                    doc_dir / "pages" / f"page-{index + 1:04d}.png",
                    format="PNG",
                    optimize=True,
                )
                page_payloads.append(
                    {"index": index, "image": page_image, "width": page_image.width,
                     "height": page_image.height}
                )
            finally:
                if bitmap is not None:
                    bitmap.close()
    finally:
        document.close()

    # ---------------- 逐页抽取版面 ----------------
    with pdfplumber.open(str(pdf_path)) as pdf:
        for index, page in enumerate(pdf.pages):
            payload = page_payloads[index]
            page_image = payload["image"]
            scale_x = page_image.width / max(1.0, float(page.width))
            scale_y = page_image.height / max(1.0, float(page.height))

            # 1) 表格区域，先算出来，之后把这些区域内的文字行剔除
            table_boxes: list[tuple[float, float, float, float]] = []
            table_blocks: list[Block] = []
            try:
                for table in page.find_tables():
                    box = tuple(float(v) for v in table.bbox)  # type: ignore[attr-defined]
                    markdown = _table_to_markdown(table.extract())
                    if not markdown:
                        continue
                    table_boxes.append(box)  # type: ignore[arg-type]
                    table_blocks.append(
                        Block(kind="table", page=index + 1, bbox=box, content=markdown)  # type: ignore[arg-type]
                    )
            except Exception as exc:  # noqa: BLE001 - 表格识别失败不影响正文
                logger.debug("第 %s 页表格识别失败: %s", index + 1, exc)

            # 2) 文字行
            lines: list[dict] = []
            try:
                raw_lines = page.extract_text_lines(strip=True) or []
            except Exception as exc:  # noqa: BLE001
                logger.debug("第 %s 页文字行抽取失败: %s", index + 1, exc)
                raw_lines = []
            for line in raw_lines:
                text = (line.get("text") or "").strip()
                if not text or _PAGE_NUMBER_RE.match(text):
                    continue
                box = (
                    float(line.get("x0", 0.0)),
                    float(line.get("top", 0.0)),
                    float(line.get("x1", 0.0)),
                    float(line.get("bottom", 0.0)),
                )
                if _inside_any(box, table_boxes):
                    continue
                sizes = [float(char.get("size", 0) or 0) for char in line.get("chars", [])]
                size = _median([value for value in sizes if value > 0], 10.0)
                lines.append({"text": text, "bbox": box, "size": size})

            # 3) 图片区域
            figure_blocks: list[Block] = []
            try:
                page_area = float(page.width) * float(page.height)
                for raw_image in page.images:
                    box = (
                        float(raw_image.get("x0", 0)),
                        float(raw_image.get("top", 0)),
                        float(raw_image.get("x1", 0)),
                        float(raw_image.get("bottom", 0)),
                    )
                    width = box[2] - box[0]
                    height = box[3] - box[1]
                    if width < 40 or height < 30:
                        continue
                    if page_area and width * height > page_area * 0.86:
                        continue  # 整页背景图/扫描底图，跳过
                    figure_seq += 1
                    rel_path = f"images/figure-{figure_seq:04d}.png"
                    pixel_box = (
                        box[0] * scale_x,
                        box[1] * scale_y,
                        box[2] * scale_x,
                        box[3] * scale_y,
                    )
                    try:
                        width_px, height_px = _crop_figure(
                            page_image, pixel_box, doc_dir / rel_path
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("裁切图片失败: %s", exc)
                        width_px, height_px = 0, 0
                    if not width_px:
                        continue
                    figure_blocks.append(
                        Block(
                            kind="image",
                            page=index + 1,
                            bbox=box,
                            image_path=rel_path,
                            meta={"width": width_px, "height": height_px},
                        )
                    )
            except Exception as exc:  # noqa: BLE001 - 图片抽取失败不影响文字
                logger.debug("第 %s 页图片抽取失败: %s", index + 1, exc)

            # 4) 图注：优先看图下方 60pt 内、以「图/表/Figure」开头的行
            consumed: set[int] = set()
            for figure in figure_blocks:
                assert figure.bbox is not None
                caption, line_index = _find_caption(lines, figure.bbox)
                if caption and line_index is not None:
                    figure.caption = caption
                    consumed.add(line_index)

            # 5) 统计字号，用于后面判断标题
            for line in lines:
                body_sizes.append(line["size"])
            for line in lines:
                if len(line["text"]) <= 60:
                    heading_sizes.append(line["size"])

            parsed.blocks.extend(figure_blocks)
            parsed.blocks.extend(table_blocks)
            body_lines = [line for i, line in enumerate(lines) if i not in consumed]
            parsed.blocks.extend(
                _group_paragraphs(body_lines, index + 1)
            )

            report(0.1 + 0.75 * (index + 1) / page_count, f"解析第 {index + 1}/{page_count} 页")

    # 释放页面图缓存
    page_payloads.clear()

    # ---------------- 标题层级推断 ----------------
    median_size = _median(body_sizes, 10.0)
    for block in parsed.blocks:
        if block.kind != "text" or block.level:
            continue
        size = float(block.meta.get("size", 0) or 0)
        if not size or len(block.content) > 70:
            continue
        if size >= median_size * 1.45:
            block.level = 1
        elif size >= median_size * 1.18:
            block.level = 2

    # 保证阅读顺序：先页码，再纵坐标（图片和表格已经按页插入）
    parsed.blocks.sort(key=lambda item: (item.page, (item.bbox or (0, 0, 0, 0))[1]))

    parsed.markdown = _to_markdown(parsed.blocks, pdf_path.stem)
    if parsed.text_chars < 50:
        parsed.warnings.append(
            "该 PDF 几乎没有可抽取的文字，可能是扫描件；已保留页面图，可直接用图片提问。"
        )
    report(0.9, "整理解析结果")
    return parsed


def _inside_any(box: tuple[float, float, float, float], areas: list[tuple[float, float, float, float]]) -> bool:
    cx = (box[0] + box[2]) / 2
    cy = (box[1] + box[3]) / 2
    return any(area[0] <= cx <= area[2] and area[1] <= cy <= area[3] for area in areas)


def _find_caption(lines: list[dict], bbox: tuple[float, float, float, float]) -> tuple[str, int | None]:
    """在图下方 70pt / 上方 40pt 内找图注。"""
    if not lines:
        return "", None
    below: tuple[float, int, dict] | None = None
    above: tuple[float, int, dict] | None = None
    for index, line in enumerate(lines):
        line_box = line["bbox"]
        vertical_gap = line_box[1] - bbox[3]
        if 0 <= vertical_gap <= 70:
            horizontal_overlap = min(line_box[2], bbox[2]) - max(line_box[0], bbox[0])
            if horizontal_overlap > 0 and (below is None or vertical_gap < below[0]):
                below = (vertical_gap, index, line)
        upward_gap = bbox[1] - line_box[3]
        if 0 <= upward_gap <= 40 and (above is None or upward_gap < above[0]):
            above = (upward_gap, index, line)

    for candidate in (below, above):
        if not candidate:
            continue
        _gap, index, line = candidate
        text = line["text"].strip()
        if _CAPTION_RE.match(text) or len(text) <= 60:
            return text, index
    return "", None


def _group_paragraphs(lines: list[dict], page: int) -> list[Block]:
    """把行合并成段落：按行距、字号变化和项目符号判断断段。"""
    if not lines:
        return []
    lines = sorted(lines, key=lambda item: (item["bbox"][1], item["bbox"][0]))
    heights = [max(1.0, line["bbox"][3] - line["bbox"][1]) for line in lines]
    median_height = _median(heights, 12.0)

    groups: list[list[dict]] = []
    current: list[dict] = []
    for line in lines:
        if not current:
            current = [line]
            continue
        previous = current[-1]
        gap = line["bbox"][1] - previous["bbox"][3]
        size_delta = abs(line["size"] - previous["size"]) / max(previous["size"], 1.0)
        starts_new = (
            gap > median_height * 1.55
            or size_delta > 0.25
            or bool(_BULLET.match(line["text"]))
            or previous["text"].endswith(("。", "！", "？", "："))
            and gap > median_height * 0.75
        )
        if starts_new:
            groups.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        groups.append(current)

    blocks: list[Block] = []
    for group in groups:
        text = "".join(item["text"] for item in group) if _should_join(group) else " ".join(
            item["text"] for item in group
        )
        text = re.sub(r"\s{2,}", " ", text).strip()
        if not text:
            continue
        bbox = (
            min(item["bbox"][0] for item in group),
            min(item["bbox"][1] for item in group),
            max(item["bbox"][2] for item in group),
            max(item["bbox"][3] for item in group),
        )
        blocks.append(
            Block(
                kind="text",
                page=page,
                bbox=bbox,
                content=text,
                meta={"size": _median([item["size"] for item in group], 10.0)},
            )
        )
    return blocks


def _should_join(group: list[dict]) -> bool:
    """中文换行直接拼接，英文/数字之间补空格。"""
    if len(group) < 2:
        return False
    return all(not item["text"].rstrip().endswith("-") for item in group[:-1])


def _to_markdown(blocks: list[Block], title: str) -> str:
    lines: list[str] = [f"# {title}", ""]
    for block in blocks:
        if block.kind == "text":
            if block.level:
                lines.append(f"{'#' * min(6, block.level + 1)} {block.content}")
            else:
                lines.append(block.content)
            lines.append("")
        elif block.kind == "image":
            caption = block.caption or "插图"
            target = block.image_path or ""
            lines.append(f"![{caption}]({target})")
            lines.append("")
        else:
            if block.caption:
                lines.append(f"**{block.caption}**")
            lines.append(block.content)
            lines.append("")
    return "\n".join(lines).strip() + "\n"
