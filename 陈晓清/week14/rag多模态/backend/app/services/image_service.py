# -*- coding: utf-8 -*-
"""图片定位、URL 生成、安全校验与 data URL 编码。"""

from __future__ import annotations

import base64
import io
import re
from pathlib import Path
from urllib.parse import quote

from PIL import Image

from ..core.config import PROCESSED_DIR

IMAGE_RE = re.compile(r"!\[(.*?)\]\((.*?)\)")


def extract_image_refs(text: str) -> list[str]:
    return [m.group(2).strip() for m in IMAGE_RE.finditer(text or "")]


def resolve_local_image(ref: str, base_dir: Path | None = None) -> Path | None:
    """把 markdown 里的图片引用解析成本地真实路径。"""
    ref = (ref or "").strip().strip('"').strip("'")
    if not ref or ref.startswith(("http://", "https://", "data:", "doc:")):
        return None

    path = Path(ref)
    candidates: list[Path] = []
    if path.is_absolute():
        candidates.append(path)
    if base_dir is not None:
        candidates.append(Path(base_dir) / path)
        candidates.append(Path(base_dir) / path.name)
    candidates.append(Path.cwd() / path)
    candidates.append(PROCESSED_DIR / path)

    for candidate in candidates:
        try:
            if candidate.exists() and candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue

    # 兜底：在 processed 目录里按文件名搜
    try:
        for found in PROCESSED_DIR.rglob(path.name):
            if found.is_file():
                return found.resolve()
    except OSError:
        pass
    return None


def image_url(path: str | Path) -> str:
    return "/api/images?path=" + quote(str(path), safe="")


def safe_image_path(raw: str) -> Path:
    """仅允许读取 PROCESSED_DIR 下的图片，防止路径穿越。"""
    if not raw:
        raise FileNotFoundError("path 不能为空")
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = (PROCESSED_DIR / candidate).resolve()
    else:
        candidate = candidate.resolve()

    root = PROCESSED_DIR.resolve()
    if root != candidate and root not in candidate.parents:
        raise PermissionError("只允许访问 backend/data/processed 下的图片")
    if not candidate.is_file():
        raise FileNotFoundError(str(candidate))
    return candidate


def to_data_url(path: str | Path, max_side: int = 1024, quality: int = 85) -> str:
    """压缩后转成 data URL，供视觉模型使用。"""
    with Image.open(path) as img:
        img = img.convert("RGB")
        img.thumbnail((max_side, max_side))
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=quality)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def normalize_answer_images(text: str, base_dir: Path | None = None) -> str:
    """把回答里的本地图片引用替换成前端可访问的 /api/images URL。"""

    def _replace(match: re.Match) -> str:
        alt, ref = match.group(1), match.group(2).strip()
        if ref.startswith(("http://", "https://", "data:")):
            return match.group(0)
        real = resolve_local_image(ref, base_dir)
        if real is None:
            return match.group(0)
        return f"![{alt}]({image_url(real)})"

    return IMAGE_RE.sub(_replace, text or "")
