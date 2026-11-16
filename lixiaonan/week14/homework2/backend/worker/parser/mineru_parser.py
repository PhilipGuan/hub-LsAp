"""MinerU 解析器实现（F1-7）：以子进程方式调用 MinerU 命令行，产出 Markdown + 图片。

兼容性说明（可通过 MINERU_CMD 环境变量整体覆盖命令模板，含 {input}/{output} 占位符）：
- MinerU 4.x:  pip install "mineru[core]"
  需先 `mineru server start`（本地解析引擎，managed 模式自动下载/加载模型），
  命令形如 `mineru parse <input> -o <out.md> --pages all --force --wait 540`；
  产物为 Markdown（页标记 `<!-- page N of M -->`，图片引用为库内 `doc:...` locator），
  图片字节用 `mineru read <locator> --format image -o <file>` 导出
- MinerU 2.x:  pip install "mineru<4"  ->  mineru -p {input} -o {output} -b pipeline
  产物目录递归找 *.md 与 *_content_list.json（图片为本地相对路径）

本解析器对两种产物形态做归一：
- 页标记/页信息统一为「### 第 N 页」标题（供 chunker 做页码映射）
- 图片统一读取为字节，source_ref 保留原引用（Worker 落盘后改写为可访问 URL）
"""
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from loguru import logger

from worker.parser.base import BaseParser, ParseError, ParsedImage, ParseResult

IMAGE_REF_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
# MinerU 4.x：库内图片引用（doc:<doc_id>/tier:.../page:N/block:M）
DOC_REF_RE = re.compile(r"^doc:")
LOCATOR_PAGE_RE = re.compile(r"page:(\d+)")
# MinerU 4.x：页标记（归一为「第 N 页」标题后交给 chunker）
PAGE_MARKER_RE = re.compile(r"<!--\s*page\s+\d+\s+of\s+(\d+)\s*-->")


class MinerUParser(BaseParser):
    name = "mineru"

    def __init__(self, settings):
        self.cmd_template = settings.mineru_cmd
        self.timeout = settings.mineru_timeout_seconds
        self.model_source = getattr(settings, "mineru_model_source", "") or ""
        self._binary = self._resolve_binary(self.cmd_template)

    # ---------- 工具 ----------
    @staticmethod
    def _resolve_binary(cmd_template: str) -> str:
        """裸命令名（如 mineru）解析为 venv 内绝对路径——Worker 子进程 PATH 不一定含 venv/bin。"""
        first = cmd_template.split(None, 1)[0] if cmd_template else "mineru"
        if first in ("mineru", "mineru-kit"):
            candidate = Path(sys.executable).parent / first
            if candidate.exists():
                return str(candidate)
        return first

    def _env(self) -> dict:
        """子进程环境：控制模型权重下载源（modelscope 国内网络更可靠）。"""
        env = dict(os.environ)
        if self.model_source:
            env["MINERU_MODEL_SOURCE"] = self.model_source
        return env

    def _run(self, cmd: str, timeout: int) -> subprocess.CompletedProcess:
        logger.info(f"[mineru] 执行命令: {cmd}")
        return subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout,
            env=self._env(),
        )

    def _export_doc_image(self, ref: str, out_path: Path) -> bytes | None:
        """MinerU 4.x：按库内 locator 导出图片字节（mineru read --format image）。"""
        cmd = (
            f"{shlex.quote(self._binary)} read {shlex.quote(ref)} "
            f"--format image -o {shlex.quote(str(out_path))}"
        )
        try:
            proc = self._run(cmd, timeout=180)
        except subprocess.TimeoutExpired:
            logger.warning(f"[mineru] 图片导出超时 {ref}")
            return None
        if proc.returncode == 0 and out_path.is_file():
            return out_path.read_bytes()
        logger.warning(f"[mineru] 图片导出失败 {ref}: {(proc.stderr or proc.stdout or '')[-200:]}")
        return None

    # ---------- 主流程 ----------
    def parse(self, pdf_path: str) -> ParseResult:
        with tempfile.TemporaryDirectory(prefix="mineru_run_") as out_dir:
            cmd = self.cmd_template.format(
                input=shlex.quote(str(pdf_path)),
                output=shlex.quote(out_dir),
            )
            # 模板里裸写的 mineru/mineru-kit 替换为解析出的绝对路径
            head, _, rest = cmd.partition(" ")
            if head in ("mineru", "mineru-kit") and rest:
                cmd = f"{shlex.quote(self._binary)} {rest}"
            try:
                proc = self._run(cmd, timeout=self.timeout)
            except subprocess.TimeoutExpired as exc:
                raise ParseError(f"MinerU 解析超时（>{self.timeout}s）") from exc
            except FileNotFoundError as exc:
                raise ParseError(f"未找到 MinerU 可执行程序，请先安装: {exc}") from exc

            if proc.returncode != 0:
                tail = ((proc.stderr or "") + (proc.stdout or ""))[-800:]
                raise ParseError(f"MinerU 解析失败(returncode={proc.returncode}): {tail}")

            md_path = self._find_output(out_dir, "*.md")
            md_text = md_path.read_text(encoding="utf-8")

            # MinerU 4.x：页标记 -> 统一「第 N 页」标题，并取总页数
            page_counts = [int(m.group(1)) for m in PAGE_MARKER_RE.finditer(md_text)]
            if page_counts:
                md_text = re.sub(
                    r"<!--\s*page\s+(\d+)\s+of\s+\d+\s*-->",
                    lambda m: f"\n\n### 第 {m.group(1)} 页\n\n",
                    md_text,
                )
            page_count = max(page_counts) if page_counts else None

            page_map = self._load_image_page_map(out_dir)  # MinerU 2.x 图片页码映射

            images: list[ParsedImage] = []
            doc_img_seq = 0
            for ref in IMAGE_REF_RE.findall(md_text):
                if DOC_REF_RE.match(ref):  # 4.x：库内 locator
                    doc_img_seq += 1
                    tmp_img = Path(out_dir) / f"docimg_{doc_img_seq}.png"
                    data = self._export_doc_image(ref, tmp_img)
                    if data is None:
                        continue
                    page_match = LOCATOR_PAGE_RE.search(ref)
                    images.append(ParsedImage(
                        data=data, ext="png", source_ref=ref,
                        page_no=int(page_match.group(1)) - 1 if page_match else None,
                    ))
                    continue
                # 2.x：本地相对路径
                img_path = (md_path.parent / ref).resolve()
                if not img_path.is_file():
                    continue  # 非本地文件（如外链）跳过
                images.append(ParsedImage(
                    data=img_path.read_bytes(),
                    ext=(img_path.suffix.lstrip(".") or "png").lower(),
                    source_ref=ref,
                    page_no=page_map.get(Path(ref).name),
                ))

            if page_count is None:
                page_count = (max(page_map.values()) + 1) if page_map else None
            logger.info(
                f"[mineru] 解析完成 file={Path(pdf_path).name} "
                f"images={len(images)} pages={page_count}"
            )
            return ParseResult(markdown=md_text, images=images, page_count=page_count)

    def _find_output(self, root: str, pattern: str) -> Path:
        files = sorted(Path(root).rglob(pattern))
        if not files:
            raise ParseError(f"MinerU 未产出 {pattern} 文件，可能解析失败")
        return files[0]

    def _load_image_page_map(self, root: str) -> dict[str, int]:
        """MinerU 2.x：从 content_list.json 提取 图片文件名 -> 页索引 映射。"""
        candidates = list(Path(root).rglob("*_content_list.json"))
        if not candidates:
            return {}
        try:
            entries = json.loads(candidates[0].read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        mapping: dict[str, int] = {}
        for entry in entries if isinstance(entries, list) else []:
            if (
                isinstance(entry, dict)
                and entry.get("type") == "image"
                and entry.get("img_path")
                and entry.get("page_idx") is not None
            ):
                mapping[Path(entry["img_path"]).name] = int(entry["page_idx"])
        return mapping
