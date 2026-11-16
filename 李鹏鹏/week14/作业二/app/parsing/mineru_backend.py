"""MinerU 解析后端。

支持两种接法
------------
1. **本地 CLI**（默认）：``mineru -p input.pdf -o outdir -b pipeline -m auto -l ch``，
   然后读取 ``*_content_list.json`` 与图片目录。命令模板可用
   ``MINERU_CMD_TEMPLATE`` 覆盖，因此 2.x / 3.x 以及自定义封装脚本都能接。
2. **HTTP 服务**：设置 ``MINERU_API_URL``（例如 ``http://127.0.0.1:8000``），
   调用 MinerU FastAPI 的 ``/file_parse``，兼容返回 ``content_list`` 或
   ``results.<file>.content_list`` 两种结构。

两种方式最终都归一化成 :class:`ParsedDocument`，下游无感知。
"""
from __future__ import annotations

import base64
import html
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import settings
from .base import Block, ParsedDocument, ParseError

logger = logging.getLogger(__name__)

_PROGRESS = Callable[[float, str], None]
_CAPTION_KEYS = ("image_caption", "img_caption", "table_caption", "caption")


def _noop_progress(_value: float, _stage: str) -> None:
    return


# --------------------------------------------------------------------------- #
# 可用性探测（带缓存，供 /api/health 与 doctor 使用）
# --------------------------------------------------------------------------- #
_probe_cache: tuple[float, bool, str] | None = None


def probe(force: bool = False) -> tuple[bool, str]:
    """检测 MinerU 是否可用，返回 (可用, 说明)。结果缓存 60 秒。"""
    global _probe_cache
    if not force and _probe_cache and time.time() - _probe_cache[0] < 60:
        return _probe_cache[1], _probe_cache[2]

    if settings.mineru_api_url:
        _probe_cache = (time.time(), True, f"HTTP 服务 {settings.mineru_api_url}")
        return True, _probe_cache[2]

    executable = shutil.which(settings.mineru_command)
    if not executable:
        _probe_cache = (time.time(), False, f"未找到命令 {settings.mineru_command}")
        return False, _probe_cache[2]
    try:
        completed = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _probe_cache = (time.time(), False, f"探测失败: {exc}")
        return False, _probe_cache[2]

    version = (completed.stdout or completed.stderr or "").strip().splitlines()
    version_text = version[0] if version else ""
    if version and version[0].strip().startswith("4."):
        message = (
            f"{version_text}（MinerU 4.x 已改为文档库 CLI，请使用 2.x/3.x 的 "
            "`mineru -p` 命令，或用 MINERU_CMD_TEMPLATE 指定命令）"
        )
        _probe_cache = (time.time(), False, message)
        return False, message

    message = f"{version_text or 'mineru'} @ {executable}"
    _probe_cache = (time.time(), True, message)
    return True, message


# --------------------------------------------------------------------------- #
# 通用：content_list → Block
# --------------------------------------------------------------------------- #
def _strip_html(text: str) -> str:
    text = re.sub(r"(?i)</t[dh]>", " | ", text)
    text = re.sub(r"(?i)</tr>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line.strip(" |") for line in lines if line.strip(" |"))


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bbox_of(item: dict[str, Any]) -> tuple[float, float, float, float] | None:
    raw = item.get("bbox")
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        return (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))
    except (TypeError, ValueError):
        return None


def _captions(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in _CAPTION_KEYS:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
        elif isinstance(value, (list, tuple)):
            parts.extend(str(entry).strip() for entry in value if str(entry).strip())
    unique: list[str] = []
    for part in parts:
        if part not in unique:
            unique.append(part)
    return "；".join(unique)[:400]


def _copy_image(source: Path, images_dir: Path, seq: int) -> tuple[str, int, int]:
    """把 MinerU 抽出的图片复制到文档目录，返回 (相对路径, 宽, 高)。"""
    suffix = source.suffix.lower() or ".png"
    target = images_dir / f"figure-{seq:04d}{suffix}"
    shutil.copyfile(source, target)
    width = height = 0
    try:
        from PIL import Image

        with Image.open(target) as image:
            width, height = image.width, image.height
    except Exception as exc:  # noqa: BLE001 - 尺寸拿不到不影响主流程
        logger.debug("读取图片尺寸失败: %s", exc)
    return f"images/{target.name}", width, height


def blocks_from_content_list(
    items: list[dict[str, Any]],
    *,
    base_dir: Path,
    images_dir: Path,
    embedded_images: dict[str, bytes] | None = None,
) -> list[Block]:
    """把 MinerU 风格 content_list 归一化为 Block 列表。"""
    blocks: list[Block] = []
    figure_seq = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "").lower()
        page = _as_int(item.get("page_idx"), 0) + 1
        bbox = _bbox_of(item)

        if kind in {"text", "equation"}:
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            try:
                level = int(item.get("text_level") or 0)
            except (TypeError, ValueError):
                level = 0
            blocks.append(Block(kind="text", page=page, bbox=bbox, content=text, level=level))
            continue

        if kind == "list":
            entries = item.get("list_items") or item.get("items") or []
            text = "\n".join(
                f"- {str(entry).strip()}" for entry in entries if str(entry).strip()
            )
            if text:
                blocks.append(Block(kind="text", page=page, bbox=bbox, content=text))
            continue

        if kind == "table":
            body = item.get("table_body") or item.get("table") or ""
            text = _strip_html(str(body)) if str(body).lstrip().startswith("<") else str(body)
            caption = _captions(item)
            if not text.strip():
                # 有些版本只给表格图片，退化成图片块
                kind = "image"
            else:
                blocks.append(
                    Block(kind="table", page=page, bbox=bbox, content=text, caption=caption)
                )
                continue

        if kind == "image":
            figure_seq += 1
            rel_path = ""
            width = height = 0
            raw_path = str(item.get("img_path") or "").strip()
            source = (base_dir / raw_path).resolve() if raw_path else None
            if source and source.is_file():
                rel_path, width, height = _copy_image(source, images_dir, figure_seq)
            elif embedded_images:
                name = Path(raw_path).name if raw_path else ""
                payload = embedded_images.get(name) or embedded_images.get(raw_path)
                if payload:
                    target = images_dir / f"figure-{figure_seq:04d}.png"
                    target.write_bytes(payload)
                    rel_path = f"images/{target.name}"
            if not rel_path:
                logger.debug("跳过找不到图片文件的 image 块: %s", raw_path)
                continue
            blocks.append(
                Block(
                    kind="image",
                    page=page,
                    bbox=bbox,
                    image_path=rel_path,
                    caption=_captions(item),
                    meta={"width": width, "height": height},
                )
            )
            continue

        # 其它类型（页眉页脚、脚注等）按正文处理，避免丢内容
        text = str(item.get("text") or "").strip()
        if text:
            blocks.append(Block(kind="text", page=page, bbox=bbox, content=text))

    return blocks


def _markdown_from_blocks(blocks: list[Block], title: str) -> str:
    lines = [f"# {title}", ""]
    for block in blocks:
        if block.kind == "text":
            heading = "#" * min(6, block.level + 1) + " " if block.level else ""
            lines += [f"{heading}{block.content}", ""]
        elif block.kind == "image":
            lines += [f"![{block.caption or '插图'}]({block.image_path})", ""]
        else:
            if block.caption:
                lines.append(f"**{block.caption}**")
            lines += [block.content, ""]
    return "\n".join(lines).strip() + "\n"


def _page_count_from_pdf(pdf_path: Path) -> int:
    try:
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(str(pdf_path))
        try:
            return len(document)
        finally:
            document.close()
    except Exception:  # noqa: BLE001
        return 0


# --------------------------------------------------------------------------- #
# 本地 CLI
# --------------------------------------------------------------------------- #
def _build_command(pdf_path: Path, out_dir: Path) -> list[str]:
    template = settings.mineru_cmd_template
    mapping = {
        "{input}": str(pdf_path),
        "{output}": str(out_dir),
        "{backend}": settings.mineru_backend,
        "{method}": settings.mineru_method,
        "{lang}": settings.mineru_lang,
    }
    tokens = shlex.split(template, posix=os.name != "nt")
    command: list[str] = []
    for token in tokens:
        replaced = token
        for placeholder, value in mapping.items():
            replaced = replaced.replace(placeholder, value)
        if "{mineru}" in replaced:
            executable = shutil.which(settings.mineru_command)
            replaced = replaced.replace("{mineru}", executable or settings.mineru_command)
        command.append(replaced)
    return command


def _find_content_list(out_dir: Path) -> Path | None:
    candidates = [path for path in out_dir.rglob("*content_list.json") if path.is_file()]
    if not candidates:
        return None
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0]


def _parse_via_cli(pdf_path: Path, doc_dir: Path, report: _PROGRESS) -> ParsedDocument:
    available, message = probe()
    if not available:
        raise ParseError(f"MinerU 不可用：{message}")

    out_dir = doc_dir / "mineru"
    try:
        if out_dir.exists():
            shutil.rmtree(out_dir, ignore_errors=True)
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ParseError(f"准备 MinerU 输出目录失败：{exc}") from exc

    command = _build_command(pdf_path, out_dir)
    logger.info("调用 MinerU: %s", " ".join(command))
    report(0.15, "MinerU 解析中（首次运行需下载模型，可能较慢）")

    started = time.time()
    try:
        process = subprocess.Popen(  # nosemgrep: python.lang.compatibility.python36.python36-compatibility-Popen1, python.lang.compatibility.python36.python36-compatibility-Popen2
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise ParseError(f"启动 MinerU 失败：{exc}") from exc

    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=5)
                break
            except subprocess.TimeoutExpired:
                elapsed = time.time() - started
                if elapsed > settings.mineru_timeout:
                    process.kill()
                    process.communicate()
                    raise ParseError(
                        f"MinerU 超过 {settings.mineru_timeout} 秒仍未完成，请检查文档大小或调大 MINERU_TIMEOUT"
                    ) from None
                report(min(0.8, 0.2 + elapsed / max(30, settings.mineru_timeout) * 0.6), "MinerU 解析中")
    finally:
        if process.poll() is None:  # pragma: no cover - 极端情况
            process.kill()

    if process.returncode != 0:
        tail = " / ".join(item for item in (stderr or stdout or "").strip().splitlines()[-4:])
        raise ParseError(f"MinerU 退出码 {process.returncode}：{tail[:600]}")

    report(0.82, "读取 MinerU 结果")
    content_list_path = _find_content_list(out_dir)
    if content_list_path is None:
        tail = " / ".join((stderr or "").strip().splitlines()[-3:])
        raise ParseError(f"MinerU 未生成 content_list.json，输出：{tail[:400]}")

    try:
        items = json.loads(content_list_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ParseError(f"读取 content_list.json 失败：{exc}") from exc
    if not isinstance(items, list):
        raise ParseError("content_list.json 格式不正确（期望数组）")

    doc_dir.joinpath("images").mkdir(parents=True, exist_ok=True)
    blocks = blocks_from_content_list(
        items,
        base_dir=content_list_path.parent,
        images_dir=doc_dir / "images",
    )
    if not blocks:
        raise ParseError("MinerU 解析完成但没有得到任何内容块")

    markdown_path = content_list_path.with_name(
        content_list_path.name.replace("_content_list.json", ".md")
    )
    markdown = ""
    if markdown_path.is_file():
        markdown = markdown_path.read_text(encoding="utf-8", errors="replace")
    if not markdown.strip():
        markdown = _markdown_from_blocks(blocks, pdf_path.stem)

    parsed = ParsedDocument(
        backend="mineru",
        page_count=_page_count_from_pdf(pdf_path) or (max(block.page for block in blocks)),
        blocks=blocks,
        markdown=markdown,
    )
    # 留存原始结果，便于排查（体积极小）
    try:
        (doc_dir / "content_list.json").write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        logger.debug("保存 content_list 副本失败: %s", exc)
    return parsed


# --------------------------------------------------------------------------- #
# HTTP 服务
# --------------------------------------------------------------------------- #
def _decode_images(raw: Any) -> dict[str, bytes]:
    """把接口返回的图片（base64 / data URL / dict）解成字节。"""
    result: dict[str, bytes] = {}
    if not isinstance(raw, dict):
        return result
    for name, value in raw.items():
        try:
            data = value
            if isinstance(value, dict):
                data = value.get("data") or value.get("base64") or value.get("content")
            if not isinstance(data, str):
                continue
            if data.startswith("data:"):
                data = data.split(",", 1)[-1]
            result[str(name)] = base64.b64decode(data)
        except (ValueError, TypeError) as exc:
            logger.debug("解析返回图片失败 %s: %s", name, exc)
    return result


def _extract_payload(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, bytes], str]:
    """兼容多种 MinerU 服务返回结构。"""
    candidates: list[Any] = []
    images: dict[str, bytes] = {}
    markdown = ""

    if isinstance(payload.get("content_list"), (list, str)):
        candidates.append(payload["content_list"])
        images = _decode_images(payload.get("images"))
        markdown = str(payload.get("md_content") or payload.get("markdown") or "")

    results = payload.get("results")
    if not candidates and isinstance(results, dict):
        for value in results.values():
            if not isinstance(value, dict):
                continue
            if isinstance(value.get("content_list"), (list, str)):
                candidates.append(value["content_list"])
                images = _decode_images(value.get("images")) or images
                markdown = markdown or str(value.get("md_content") or "")
                break

    if not candidates:
        raise ParseError("MinerU 服务返回结果中没有 content_list 字段")

    raw = candidates[0]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise ParseError(f"content_list 不是合法 JSON：{exc}") from exc
    if not isinstance(raw, list):
        raise ParseError("content_list 格式不正确（期望数组）")
    return raw, images, markdown


def _parse_via_api(pdf_path: Path, doc_dir: Path, report: _PROGRESS) -> ParsedDocument:
    try:
        import httpx
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise ParseError("使用 MINERU_API_URL 需要安装 httpx") from exc

    url = settings.mineru_api_url.rstrip("/") + "/file_parse"
    headers = {"Accept": "application/json"}
    if settings.mineru_api_token:
        headers["Authorization"] = f"Bearer {settings.mineru_api_token}"
    data = {
        "backend": settings.mineru_backend,
        "parse_method": settings.mineru_method,
        "lang_list": settings.mineru_lang,
        "return_content_list": "true",
        "return_md": "true",
        "return_images": "true",
    }
    report(0.2, "上传 PDF 到 MinerU 服务")
    timeout = httpx.Timeout(connect=20.0, read=settings.mineru_timeout, write=120.0, pool=20.0)
    try:
        with httpx.Client(timeout=timeout) as client, pdf_path.open("rb") as handle:
            response = client.post(
                url,
                files={"file": (pdf_path.name, handle, "application/pdf")},
                data=data,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise ParseError(f"调用 MinerU 服务失败：{exc}") from exc

    if response.status_code >= 400:
        raise ParseError(
            f"MinerU 服务返回 HTTP {response.status_code}：{response.text[:400]}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ParseError(f"MinerU 服务返回的不是 JSON：{response.text[:300]}") from exc

    report(0.8, "解析 MinerU 服务结果")
    items, images, markdown = _extract_payload(payload)
    doc_dir.joinpath("images").mkdir(parents=True, exist_ok=True)
    blocks = blocks_from_content_list(
        items,
        base_dir=doc_dir,
        images_dir=doc_dir / "images",
        embedded_images=images,
    )
    if not blocks:
        raise ParseError("MinerU 服务没有返回可用内容块")

    return ParsedDocument(
        backend="mineru-api",
        page_count=_page_count_from_pdf(pdf_path) or max(block.page for block in blocks),
        blocks=blocks,
        markdown=markdown or _markdown_from_blocks(blocks, pdf_path.stem),
    )


# --------------------------------------------------------------------------- #
# 对外入口
# --------------------------------------------------------------------------- #
def parse(pdf_path: Path, doc_dir: Path, progress: _PROGRESS | None = None) -> ParsedDocument:
    report = progress or _noop_progress
    if settings.mineru_api_url:
        return _parse_via_api(pdf_path, doc_dir, report)
    return _parse_via_cli(pdf_path, doc_dir, report)
