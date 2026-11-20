"""
PDF 解析：调用 MinerU 把 PDF 转成 markdown + 图片。

MinerU 不同版本的 CLI 差异较大，这里支持三种模式（默认 auto 自动识别）：

1. flat —— MinerU 2.x / 3.x 的扁平命令
       mineru -p input.pdf -o processed -b pipeline

2. kit  —— MinerU 4.x 的一次性转换命令（推荐，能带出 images 目录）
       mineru-kit parse <input_dir> -o <out_dir> --format zip --pages all
   输出的 zip 中包含 markdown.md + images/，解压后即可使用。

3. parse —— MinerU 4.x 的 `mineru parse` 子命令
       mineru parse input.pdf --pages all -o output.md
   注意：该命令只写 markdown，不落盘图片（图片要通过 mineru read 的 locator 获取），
   因此只能作为没有 mineru-kit 时的兜底方案。

另外提供 PyMuPDF 兜底实现（不依赖 GPU / MinerU），方便本机先把流程跑通。
"""
import logging
import shlex
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Tuple

from config import (
    MINERU_API_URL,
    MINERU_BACKEND,
    MINERU_CMD,
    MINERU_EXTRA_ARGS,
    MINERU_KIT_CMD,
    MINERU_TIER,
    PARSE_TIMEOUT,
    PARSER_BACKEND,
    PARSER_MODE,
    PROCESSED_DIR,
)

logger = logging.getLogger(__name__)

_resolved_mode = None


class ParseError(RuntimeError):
    """解析失败（命令不存在、超时、产物缺失等）。"""


def parse_pdf(filepath: Path) -> Tuple[Path, Path]:
    """
    解析 PDF。

    Returns:
        (markdown 文件路径, markdown 所在目录)
    """
    filepath = Path(filepath)
    if not filepath.is_file():
        raise ParseError(f"文件不存在: {filepath}")

    if PARSER_BACKEND == "pymupdf":
        return _parse_with_pymupdf(filepath)

    mode = _resolve_mode()
    if mode == "kit":
        return _parse_with_kit(filepath)
    if mode == "parse":
        return _parse_with_parse_cli(filepath)
    return _parse_with_flat_cli(filepath)


# ---------------------------------------------------------------------------
# CLI 模式识别
# ---------------------------------------------------------------------------
def _resolve_mode() -> str:
    """返回 flat / kit / parse；auto 模式下自动探测 MinerU 版本。"""
    global _resolved_mode
    if PARSER_MODE in ("flat", "kit", "parse"):
        return PARSER_MODE
    if _resolved_mode:
        return _resolved_mode

    if shutil.which(MINERU_KIT_CMD):
        _resolved_mode = "kit"
    else:
        help_text = ""
        try:
            proc = subprocess.run(
                [MINERU_CMD, "--help"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            help_text = (proc.stdout or "") + (proc.stderr or "")
        except Exception:
            pass
        # 4.x 的帮助信息是 "Usage: mineru [OPTIONS] COMMAND [ARGS]..."
        _resolved_mode = (
            "parse" if ("COMMAND" in help_text or "Commands:" in help_text) else "flat"
        )

    logger.info("MinerU CLI 模式: %s", _resolved_mode)
    return _resolved_mode


def _run(cmd: list, label: str):
    logger.info("执行 %s: %s", label, " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=PARSE_TIMEOUT,
        )
    except FileNotFoundError as exc:
        raise ParseError(
            f"找不到命令 `{cmd[0]}`。请安装对应版本 MinerU，"
            "或设置 PARSER_BACKEND=pymupdf 使用兜底解析。"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ParseError(f"{label} 解析超时（>{PARSE_TIMEOUT} 秒）") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-2000:]
        raise ParseError(f"{label} 退出码 {result.returncode}: {detail}")
    return result


def _extra_args() -> list:
    return shlex.split(MINERU_EXTRA_ARGS) if MINERU_EXTRA_ARGS else []


# ---------------------------------------------------------------------------
# 模式 1：MinerU 2.x / 3.x 扁平命令
# ---------------------------------------------------------------------------
def _parse_with_flat_cli(filepath: Path) -> Tuple[Path, Path]:
    cmd = [
        MINERU_CMD,
        "-p",
        str(filepath),
        "-o",
        str(PROCESSED_DIR),
        "-b",
        MINERU_BACKEND,
    ]
    # vlm-http-client / vlm-transformers 等后端可指定服务地址
    if MINERU_BACKEND.startswith("vlm") and MINERU_API_URL:
        cmd += ["-u", MINERU_API_URL]
    cmd += _extra_args()

    _run(cmd, "MinerU")

    md_path = _find_markdown(filepath.stem)
    if md_path is None:
        raise ParseError(
            f"MinerU 执行成功，但在 {PROCESSED_DIR / filepath.stem} 下没有找到 markdown"
        )
    logger.info("MinerU 解析完成: %s", md_path)
    return md_path, md_path.parent


# ---------------------------------------------------------------------------
# 模式 2：MinerU 4.x mineru-kit（zip 内含 markdown + images）
# ---------------------------------------------------------------------------
def _parse_with_kit(filepath: Path) -> Tuple[Path, Path]:
    stem = filepath.stem
    out_dir = PROCESSED_DIR / stem
    out_dir.mkdir(parents=True, exist_ok=True)

    # 清掉上次失败可能残留的 zip，避免误用旧产物
    for old_zip in out_dir.rglob("*.zip"):
        try:
            old_zip.unlink()
        except OSError:
            pass

    # mineru-kit 对“目录输入”的 -o 语义最明确：目录输出 + <stem>.zip
    input_dir = out_dir / "_input"
    if input_dir.exists():
        shutil.rmtree(input_dir, ignore_errors=True)
    input_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(filepath, input_dir / filepath.name)

    cmd = [
        MINERU_KIT_CMD,
        "parse",
        str(input_dir),
        "-o",
        str(out_dir),
        "--format",
        "zip",
        "--pages",
        "all",
    ]
    if MINERU_TIER:
        cmd += ["--tier", MINERU_TIER]
    cmd += _extra_args()

    _run(cmd, "mineru-kit")

    zip_path = _find_zip(out_dir)
    if zip_path is None:
        # 个别版本可能直接产出 markdown
        md_path = _find_markdown(stem)
        if md_path is not None:
            logger.info("mineru-kit 解析完成（markdown）: %s", md_path)
            shutil.rmtree(input_dir, ignore_errors=True)
            return md_path, md_path.parent
        raise ParseError("mineru-kit 执行成功，但既没有找到 zip，也没有找到 markdown")

    extract_dir = out_dir / "extracted"
    if extract_dir.exists():
        shutil.rmtree(extract_dir, ignore_errors=True)
    extract_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_dir)
    except zipfile.BadZipFile as exc:
        raise ParseError(f"mineru-kit 输出不是有效的 zip: {zip_path}") from exc

    md_path = _find_markdown_in(extract_dir)
    if md_path is None:
        raise ParseError(f"mineru-kit 的 zip 中没有找到 markdown: {zip_path}")

    # 清理中间产物，只保留解压后的 markdown + images
    try:
        zip_path.unlink(missing_ok=True)
    except OSError:
        pass
    shutil.rmtree(input_dir, ignore_errors=True)

    logger.info("mineru-kit 解析完成: %s", md_path)
    return md_path, md_path.parent


# ---------------------------------------------------------------------------
# 模式 3：MinerU 4.x `mineru parse`（只输出 markdown，无本地图片）
# ---------------------------------------------------------------------------
def _parse_with_parse_cli(filepath: Path) -> Tuple[Path, Path]:
    stem = filepath.stem
    out_dir = PROCESSED_DIR / stem
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{stem}.md"

    cmd = [
        MINERU_CMD,
        "parse",
        str(filepath),
        "--pages",
        "all",
        "-o",
        str(md_path),
    ]
    cmd += _extra_args()

    _run(cmd, "mineru parse")

    if md_path.is_file():
        logger.warning(
            "当前使用 `mineru parse`，它不会落盘图片；"
            "如需图文问答，请安装/使用 mineru-kit。"
        )
        return md_path, md_path.parent

    found = _find_markdown(stem)
    if found is None:
        raise ParseError("mineru parse 执行成功，但没有找到 markdown")
    return found, found.parent


# ---------------------------------------------------------------------------
# 产物查找
# ---------------------------------------------------------------------------
def _find_markdown(stem: str):
    """在 processed/<stem> 下找 markdown，优先 auto 目录 / 最短路径。"""
    root = PROCESSED_DIR / stem
    if not root.is_dir():
        return None
    return _find_markdown_in(root)


def _find_markdown_in(root: Path):
    candidates = list(root.rglob("*.md"))
    if not candidates:
        return None
    candidates.sort(
        key=lambda p: (
            p.name != "markdown.md",   # 4.x 常见命名
            p.parent.name != "auto",   # 2.x/3.x 常见目录
            len(str(p)),
        )
    )
    return candidates[0]


def _find_zip(root: Path):
    candidates = list(root.rglob("*.zip"))
    if not candidates:
        return None
    candidates.sort(key=lambda p: len(str(p)))
    return candidates[0]


# ---------------------------------------------------------------------------
# PyMuPDF 兜底
# ---------------------------------------------------------------------------
def _parse_with_pymupdf(filepath: Path) -> Tuple[Path, Path]:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise ParseError("未安装 PyMuPDF，请执行 pip install pymupdf") from exc

    out_dir = PROCESSED_DIR / filepath.stem
    image_dir = out_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{filepath.stem}.md"

    parts = []
    try:
        with fitz.open(str(filepath)) as document:
            for page_no, page in enumerate(document, start=1):
                parts.append(f"\n## 第 {page_no} 页\n")
                text = page.get_text("text").strip()
                if text:
                    parts.append(text)

                for image_no, image in enumerate(page.get_images(full=True)):
                    xref = image[0]
                    try:
                        extracted = document.extract_image(xref)
                    except Exception:
                        continue
                    ext = extracted.get("ext", "png")
                    name = f"p{page_no}_{image_no}.{ext}"
                    (image_dir / name).write_bytes(extracted["image"])
                    parts.append(f"\n![第{page_no}页图片{image_no + 1}](images/{name})\n")
    except Exception as exc:
        raise ParseError(f"PyMuPDF 解析失败: {exc}") from exc

    md_path.write_text("\n".join(parts), encoding="utf-8")
    logger.info("PyMuPDF 解析完成: %s", md_path)
    return md_path, out_dir
