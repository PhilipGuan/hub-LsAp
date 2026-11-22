# -*- coding: utf-8 -*-
"""MinerU 调用：PDF -> markdown，并把 doc: 图片定位符落成本地图片。"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from ..core.config import (
    BACKEND_DIR,
    MINERU_BIN,
    MINERU_TIER,
    MINERU_TIMEOUT,
    PROCESSED_DIR,
)

PAGE_RE = re.compile(r"<!--\s*page\s+(\d+)\s+of\s+(\d+)\s*-->")
DOC_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((doc:[^)]+)\)")


@dataclass
class ParseResult:
    markdown: str
    page_count: int
    md_path: Path
    output_dir: Path


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(BACKEND_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _mineru_available() -> bool:
    return shutil.which(MINERU_BIN) is not None


def ensure_server(timeout_sec: int = 90) -> None:
    """MinerU 4.x 的 parse/read 依赖本地 doclib 服务。"""
    if not _mineru_available():
        raise RuntimeError(
            "找不到 mineru 命令。请先安装 MinerU：pip install -U \"mineru[core]\"，"
            "并确认 `mineru --version` 可用。"
        )

    status = _run([MINERU_BIN, "server", "status"], timeout=30)
    if status.returncode == 0:
        return

    start = _run([MINERU_BIN, "server", "start"], timeout=120)
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        status = _run([MINERU_BIN, "server", "status"], timeout=30)
        if status.returncode == 0:
            return
        time.sleep(3)

    detail = (start.stderr or start.stdout or "").strip()[-500:]
    raise RuntimeError(f"MinerU 本地服务启动失败：{detail or '未知原因'}")


def _count_pages(pdf_path: Path) -> int:
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(pdf_path)).pages)
    except Exception:
        return 0


def _find_markdown(out_md: Path) -> Path:
    if out_md.is_file():
        return out_md
    if out_md.is_dir():
        candidates = sorted(out_md.rglob("*.md"))
        if candidates:
            return candidates[0]
    raise FileNotFoundError(f"MinerU 执行完成，但没有找到 markdown 输出：{out_md}")


def _extract_doc_images(markdown: str, output_dir: Path) -> str:
    """把 ![](doc:...) 逐个导出为图片，并替换成绝对路径。"""
    locators: dict[str, str] = {}
    for match in DOC_IMAGE_RE.finditer(markdown):
        locators.setdefault(match.group(2), match.group(1))

    if not locators:
        return markdown

    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    replacements: dict[str, str] = {}
    for index, (locator, alt) in enumerate(locators.items(), 1):
        target = images_dir / f"image_{index}.png"
        try:
            result = _run(
                [MINERU_BIN, "read", locator, "--format", "image", "-o", str(target)],
                timeout=120,
            )
            if result.returncode == 0 and target.is_file() and target.stat().st_size > 0:
                replacements[locator] = f"![{alt}]({target.as_posix()})"
                continue
        except Exception:
            pass
        replacements[locator] = f"[图片提取失败：{alt}]"

    def _repl(match: re.Match) -> str:
        return replacements.get(match.group(2), match.group(0))

    return DOC_IMAGE_RE.sub(_repl, markdown)


def parse_pdf(pdf_path: str | Path, file_id: int) -> ParseResult:
    """调用 MinerU 解析 PDF，返回 markdown 文本与页数。"""
    ensure_server()

    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF 不存在：{pdf_path}")

    output_dir = PROCESSED_DIR / str(file_id)
    if output_dir.exists():
        shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_md = output_dir / "content.md"
    if out_md.exists():
        out_md.unlink()

    cmd = [
        MINERU_BIN,
        "parse",
        str(pdf_path),
        "-o",
        str(out_md),
        "--format",
        "markdown",
        "--tier",
        MINERU_TIER,
    ]
    result = _run(cmd, timeout=MINERU_TIMEOUT)

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if "Local parse-server is disabled" in detail:
            raise RuntimeError(
                "当前 MinerU 档位需要本地解析服务。请把 backend/.env 里的 "
                "MINERU_TIER 改为 flash，或先启用 MinerU 本地 parse-server。"
            )
        raise RuntimeError(f"MinerU 解析失败：{detail[-1000:]}")

    md_path = _find_markdown(out_md)
    markdown = md_path.read_text(encoding="utf-8", errors="ignore")
    markdown = _extract_doc_images(markdown, output_dir)
    md_path.write_text(markdown, encoding="utf-8")

    pages = [int(m.group(1)) for m in PAGE_RE.finditer(markdown)]
    page_count = max(pages) if pages else _count_pages(pdf_path)

    return ParseResult(
        markdown=markdown,
        page_count=page_count,
        md_path=md_path,
        output_dir=output_dir,
    )
