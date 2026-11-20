"""测试辅助：生成最小可用 PDF / PNG，避免引入 reportlab 等重依赖。"""
from __future__ import annotations

from pathlib import Path


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_text_pdf(path: Path, lines: list[str], title: str = "Retrieval Augmented Generation") -> Path:
    """生成单页、带真实文本层的 PDF（Helvetica 基础字体，无需嵌入）。"""
    stream_lines = [
        "BT",
        "/F1 15 Tf",
        "72 780 Td",
        f"({_escape(title)}) Tj",
        "/F1 11 Tf",
        "0 -26 Td",
    ]
    for line in lines:
        stream_lines.append(f"({_escape(line)}) Tj")
        stream_lines.append("0 -18 Td")
    stream_lines.append("ET")
    stream = "\n".join(stream_lines)

    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(stream.encode('latin-1'))} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]

    buffer = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(buffer))
        buffer += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")

    xref_offset = len(buffer)
    buffer += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
    buffer += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        buffer += f"{offset:010d} 00000 n \n".encode("latin-1")
    buffer += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    ).encode("latin-1")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(buffer))
    return path


def make_png(path: Path, size: tuple[int, int] = (72, 48), color: str = "#2563eb") -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, format="PNG")
    return path
