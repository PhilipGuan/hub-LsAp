"""调用本地 MinerU 4.x 解析 PDF，并把文档库图片定位符导出为真实图片文件。"""

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..config import get_settings

log = logging.getLogger(__name__)

PAGE_RE = re.compile(r"^<!--\s*page\s+(\d+)\s+of\s+(\d+)\s*-->$")
IMG_MD_RE = re.compile(r"!\[([^\]]*)\]\((doc:[^)]+)\)")
LOCATOR_RE = re.compile(r"doc:([0-9a-f]+)/tier:([^/]+)/page:(\d+)/block:(\d+)")


@dataclass
class ParsedDocument:
    markdown: str
    page_count: int
    image_count: int
    work_dir: Path


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    log.info("run: %s", " ".join(cmd))
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _parse_cmd(settings, pdf_path: Path, md_path: Path) -> list[str]:
    return [
        settings.mineru_bin,
        "parse",
        str(pdf_path),
        "-o",
        str(md_path),
        "--tier",
        settings.mineru_tier,
        "--pages",
        "all",
        "--force",
    ]


def parse_pdf(pdf_path: Path, doc_id: str) -> ParsedDocument:
    settings = get_settings()
    work_dir = settings.parsed_dir / doc_id
    work_dir.mkdir(parents=True, exist_ok=True)
    md_path = work_dir / "doc.md"
    images_dir = work_dir / "images"
    images_dir.mkdir(exist_ok=True)

    result = _run(_parse_cmd(settings, pdf_path, md_path), timeout=settings.mineru_timeout)
    if result.returncode != 0 and "服务未运行" in (result.stderr + result.stdout):
        log.warning("MinerU local service down, starting it...")
        _run([settings.mineru_bin, "server", "start"], timeout=180)
        result = _run(_parse_cmd(settings, pdf_path, md_path), timeout=settings.mineru_timeout)

    if result.returncode != 0 or not md_path.exists():
        tail = (result.stderr or result.stdout or "").strip()[-1200:]
        raise RuntimeError(f"MinerU parse failed (exit={result.returncode}): {tail}")

    markdown = md_path.read_text(encoding="utf-8", errors="replace")

    # 1) 把 doc:xxx/tier:flash/page:N/block:M 导出为 PNG
    exported: dict[str, str] = {}
    for alt, locator in IMG_MD_RE.findall(markdown):
        m = LOCATOR_RE.search(locator)
        if not m:
            continue
        _doc, _tier, page, block = m.groups()
        filename = f"page{page}_block{block}.png"
        target = images_dir / filename
        if not target.exists():
            r = _run(
                [
                    settings.mineru_bin,
                    "read",
                    locator,
                    "--format",
                    "image",
                    "-o",
                    str(target),
                ],
                timeout=180,
            )
            if r.returncode != 0 or not target.exists():
                log.warning("export image failed: %s -> %s", locator, (r.stderr or "")[-300:])
                continue
        exported[locator] = f"/static/parsed/{doc_id}/images/{filename}"

    # 2) 改写 Markdown 中的图片链接为可访问的静态 URL
    def _replace(match: re.Match) -> str:
        alt, locator = match.group(1), match.group(2)
        url = exported.get(locator)
        return f"![{alt}]({url})" if url else ""

    markdown = IMG_MD_RE.sub(_replace, markdown)
    md_path.write_text(markdown, encoding="utf-8")

    pages = [int(m.group(1)) for m in PAGE_RE.finditer(markdown)]
    page_count = max(pages) if pages else 0
    if page_count == 0:
        last = PAGE_RE.search(markdown.strip().splitlines()[-1] if markdown.strip() else "")
        page_count = int(last.group(2)) if last else 1

    return ParsedDocument(
        markdown=markdown,
        page_count=page_count,
        image_count=len(exported),
        work_dir=work_dir,
    )
