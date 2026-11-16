"""
文本 / markdown / 图片路径相关的小工具。

MinerU 输出的 markdown 里图片是相对路径，例如：

    ![图](images/abc.jpg)

而 RAG 检索结果需要把图片换成“绝对路径”，前端才能找到并展示，
大模型也才能把图片链接原样放进回答里。
"""
import base64
import mimetypes
import re
from pathlib import Path
from typing import Iterator, List, Optional, Tuple, Union

# ![alt](url)；url 允许包含空格（Windows 路径常见）
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(\s*([^)]+?)\s*\)")

# MinerU 会在文末生成参考文献索引 "[1] xxx"，对问答帮助不大
_REF_LINE_RE = re.compile(r"^\[\d+\]")


def extract_image_urls(markdown: str) -> List[str]:
    return [m.group(2).strip() for m in IMAGE_RE.finditer(markdown or "")]


def clean_text_for_embedding(markdown: str) -> str:
    """去掉图片 markdown 语法，只保留可读文本用于向量化。"""
    text = IMAGE_RE.sub(" ", markdown or "")
    return re.sub(r"[ \t]+", " ", text).strip()


def resolve_image_url(url: str, base_dir: Union[str, Path, None]) -> str:
    """
    把 markdown 里的相对图片地址转换成绝对地址。

    - http(s) / data: 原样返回
    - 绝对路径：原样返回
    - 相对路径：拼到 base_dir 上（MinerU 的 markdown 与 images 目录同级）
    """
    url = (url or "").strip()
    if not url:
        return url
    if url.startswith(("http://", "https://", "data:")):
        return url

    path = Path(url)
    if path.is_absolute():
        return str(path)

    if base_dir:
        candidate = Path(base_dir) / path
        if candidate.exists():
            return str(candidate)
        return str(candidate)
    return url


def rewrite_relative_images(markdown: str, base_dir: Union[str, Path, None]) -> str:
    """把所有相对图片链接改写为绝对路径，保留 markdown 结构不变。"""

    def _replace(match: re.Match) -> str:
        alt, url = match.group(1), match.group(2)
        return f"![{alt}]({resolve_image_url(url, base_dir)})"

    return IMAGE_RE.sub(_replace, markdown or "")


def _is_image_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("![") and IMAGE_RE.match(stripped) is not None


def split_markdown_chunks(markdown: str, chunk_size: int = 256) -> List[str]:
    """
    按行把 markdown 切成若干个 chunk。

    规则：
    - 空行跳过；参考文献索引行跳过
    - 图片行尽量和前文放进同一个 chunk（图片通常是前文的配图）
    - 单个 chunk 超过 chunk_size 后换新块
    """
    chunks: List[str] = []
    current = ""

    for raw_line in (markdown or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("# References"):
            continue
        if _REF_LINE_RE.match(line):
            continue

        # 图片行：优先并入当前块，保证图片和上下文在一起
        if _is_image_line(line):
            current = f"{current}\n{line}" if current else line
            continue

        # 超长单行（有些 PDF 抽出来一整页没有换行）：先切段，避免单个 chunk 过大
        if len(line) > chunk_size * 2:
            if current:
                chunks.append(current)
                current = ""
            step = chunk_size * 2
            chunks.extend(line[i : i + step] for i in range(0, len(line), step))
            continue

        if not current:
            current = line
        elif len(current) + len(line) + 1 <= chunk_size:
            current = f"{current}\n{line}"
        else:
            chunks.append(current)
            current = line

        # 防止单个超长段落把 chunk 撑得过大
        if len(current) >= chunk_size * 2:
            chunks.append(current)
            current = ""

    if current:
        chunks.append(current)

    return [c.strip() for c in chunks if c.strip()]


def iter_markdown_segments(markdown: str) -> Iterator[Tuple[str, str, Optional[str]]]:
    """
    把 markdown 拆成 ("text", 文本, None) 和 ("image", 图片地址, alt) 两类片段，
    供前端逐段渲染（文字用 st.markdown，图片用 st.image）。
    """
    position = 0
    for match in IMAGE_RE.finditer(markdown or ""):
        if match.start() > position:
            yield ("text", markdown[position:match.start()], None)
        yield ("image", match.group(2).strip(), match.group(1))
        position = match.end()
    if position < len(markdown or ""):
        yield ("text", markdown[position:], None)


def resolve_image_path(
    url: str, search_dirs: Optional[List[Union[str, Path]]] = None
) -> Optional[str]:
    """
    给前端用：根据 markdown 里的地址找到一个真正可读取的图片路径。

    依次尝试：http/data 原样返回 -> 绝对路径 -> 相对当前目录 ->
    search_dirs/url -> search_dirs/images/文件名 -> 在 search_dirs 下按文件名搜索。
    找不到返回 None。
    """
    url = (url or "").strip()
    if not url:
        return None
    if url.startswith(("http://", "https://", "data:")):
        return url

    path = Path(url)
    if path.is_absolute():
        return str(path) if path.is_file() else None
    if path.is_file():
        return str(path)

    dirs = [Path(d) for d in (search_dirs or []) if d]
    for directory in dirs:
        for candidate in (
            directory / path,
            directory / "images" / path.name,
            directory / path.name,
        ):
            if candidate.is_file():
                return str(candidate)

    for directory in dirs:
        if not directory.is_dir():
            continue
        for candidate in directory.rglob(path.name):
            if candidate.is_file():
                return str(candidate)
    return None


def file_to_data_url(path: Union[str, Path]) -> str:
    """把本地图片转成 data URL，供多模态大模型直接读取。"""
    path = Path(path)
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def bytes_to_data_url(data: bytes, mime: str = "image/png") -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def find_local_images(
    markdown: str, base_dir: Union[str, Path, None], limit: int = 3
) -> List[str]:
    """从一段 markdown 中找出真实存在的本地图片，最多返回 limit 张。"""
    results: List[str] = []
    for url in extract_image_urls(markdown):
        resolved = resolve_image_path(url, [base_dir] if base_dir else [])
        if resolved and not resolved.startswith(("http://", "https://", "data:")):
            if resolved not in results:
                results.append(resolved)
        if len(results) >= limit:
            break
    return results
