"""调用 mineru-kit 解析 PDF，并解压解析结果。"""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Dict


def _mineru_cmd() -> list:
    """优先用 PATH 里的 mineru-kit，否则退回 python -m mineru_kit。"""
    if shutil.which("mineru-kit"):
        return ["mineru-kit"]
    return [sys.executable, "-m", "mineru_kit"]


def parse_pdf(pdf_path: str | Path, out_dir: str | Path) -> Dict:
    """解析单个 PDF，结果解压到 out_dir。

    返回 {"markdown": markdown.md 路径, "images_dir": images 目录路径}
    """
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = _mineru_cmd() + [
        "parse", str(pdf_path),
        "-o", str(out_dir),
        "--format", "zip",
        "--tier", "standard",
    ]

    log_file = out_dir / "mineru.log"
    with open(log_file, "w", encoding="utf-8") as logf:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        for line in proc.stdout:
            logf.write(line)
            logf.flush()
        ret = proc.wait()
    if ret != 0:
        raise RuntimeError(f"mineru-kit 执行失败，返回码 {ret}，日志: {log_file}")

    # 找最新生成的 zip
    zips = sorted(
        out_dir.rglob("*.zip"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not zips:
        raise FileNotFoundError(f"{out_dir} 下未找到解析结果 .zip")
    zip_path = zips[0]

    # 解压到独立子目录（兼容中文文件名的 GBK 乱码）
    unzip_dir = out_dir / f"{zip_path.stem}_unzipped"
    unzip_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            if not (info.flag_bits & 0x800):
                try:
                    info.filename = info.filename.encode("cp437").decode("gbk")
                except UnicodeDecodeError:
                    pass
            zf.extract(info, path=unzip_dir)

    markdown = unzip_dir / "markdown.md"
    if not markdown.exists():
        # 部分版本解析结果命名为 <pdf名>.md
        md_files = sorted(unzip_dir.glob("*.md"))
        if not md_files:
            raise FileNotFoundError(f"解压结果中未找到 markdown 文件: {unzip_dir}")
        markdown = md_files[0]

    return {"markdown": markdown, "images_dir": unzip_dir / "images"}
