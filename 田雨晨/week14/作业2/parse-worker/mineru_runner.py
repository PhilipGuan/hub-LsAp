"""MinerU 解析调用：真实 HTTP 服务 + 离线 mock（本地没有 MinerU 时也能把链路跑通）。

在链路中的位置：解析工作器 `worker.process_document` 先下载 PDF，再调用这里的解析器，
得到 Markdown/页数/图片，随后交给 chunker 切片、indexer 入库。没有 `MINERU_API_URL`
时用离线 mock 生成固定的多页 Markdown + 一张示意图，保证本地全链路可跑。

真实 MinerU 接口约定（第一版最小假设，按实际 MinerU 服务调整）：
    POST {MINERU_API_URL}/file_parse   multipart 字段 file=<pdf>
    返回 JSON：{"markdown": "...", "pages": 3, "images": [{"name", "page", "bbox", "caption", "data_base64"}]}
"""

if __package__ in (None, ""):  # 允许直接运行本文件
    import sys
    from pathlib import Path

    for _root in Path(__file__).resolve().parents:
        if (_root / "backend" / "app").is_dir():
            sys.path.insert(0, str(_root))
            break

import base64
from dataclasses import dataclass, field
from typing import Protocol

import requests

# 1x1 PNG：没有 PyMuPDF 时 mock 只能用它占位（前端已限制图片最大高度，不会拉满整行）
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

_MOCK_IMAGE_CACHE: bytes | None = None


def placeholder_image_png() -> bytes:
    """生成 mock 用的“系统架构图”占位 PNG（640x360，带说明文字）。

    mock 解析没有真实图片，之前返回 1x1 像素，前端放大后就是一整块纯色；
    这里直接画一张可读的示意图；没有 PyMuPDF 的机器退回 1x1。
    """
    global _MOCK_IMAGE_CACHE
    if _MOCK_IMAGE_CACHE is not None:
        return _MOCK_IMAGE_CACHE
    try:
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz
        document = fitz.open()
        page = document.new_page(width=640, height=360)
        page.draw_rect(fitz.Rect(0, 0, 640, 360), color=(0.78, 0.86, 1.0), fill=(0.94, 0.97, 1.0))
        page.insert_text((48, 120), "System Architecture (mock)", fontsize=26, color=(0.15, 0.3, 0.62))
        page.insert_text(
            (48, 165),
            "MinerU is not configured: this is a placeholder image.",
            fontsize=13,
            color=(0.32, 0.38, 0.5),
        )
        page.insert_text(
            (48, 190),
            "Set MINERU_API_URL to extract real images from PDFs.",
            fontsize=13,
            color=(0.32, 0.38, 0.5),
        )
        pixmap = page.get_pixmap()
        _MOCK_IMAGE_CACHE = pixmap.tobytes("png")
        document.close()
        return _MOCK_IMAGE_CACHE
    except ImportError:
        _MOCK_IMAGE_CACHE = TINY_PNG
        return _MOCK_IMAGE_CACHE


@dataclass
class MineruImage:
    """MinerU 抽取出的一张图片。"""

    name: str
    data: bytes
    page: int = 1
    bbox: list[float] | None = None
    asset_type: str = "figure"
    caption: str = ""


@dataclass
class MineruResult:
    """MinerU 解析产物：Markdown + 页数 + 抽取图片。"""

    markdown: str
    pages: int
    images: list[MineruImage] = field(default_factory=list)


class MineruParser(Protocol):
    """解析器最小接口：PDF 字节 + 文件名 → 解析产物（真实服务与 mock 都实现它）。"""

    def parse(self, pdf: bytes, filename: str) -> MineruResult:
        """解析一份 PDF；失败直接抛异常，由工作器转成状态机失败。"""
        ...


class HttpMineruParser:
    """调用真实 MinerU 服务。"""

    def __init__(self, base_url: str, timeout_s: float = 300.0) -> None:
        """保存服务地址与超时；地址为空直接报错（配置问题早暴露）。"""
        if not base_url:
            raise ValueError("MINERU_API_URL 为空")
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def parse(self, pdf: bytes, filename: str) -> MineruResult:
        """POST /file_parse，把返回 JSON 转成 MineruResult（缺字段用默认值兜底）。"""
        response = requests.post(
            f"{self.base_url}/file_parse",
            files={"file": (filename or "document.pdf", pdf, "application/pdf")},
            timeout=self.timeout_s,
        )
        response.raise_for_status()
        payload = response.json()
        markdown = payload.get("markdown") or ""
        # 服务没给页数时，用 Markdown 里的页码标记数量推断
        pages = int(payload.get("pages") or max(1, markdown.count("<!-- page:")))
        images = [
            MineruImage(
                name=item.get("name") or f"figure-{index + 1}.png",
                data=base64.b64decode(item.get("data_base64") or ""),
                page=int(item.get("page") or 1),
                bbox=item.get("bbox"),
                asset_type=item.get("asset_type") or "figure",
                caption=item.get("caption") or "",
            )
            for index, item in enumerate(payload.get("images") or [])
            if item.get("data_base64")
        ]
        return MineruResult(markdown=markdown, pages=pages, images=images)


class MockMineruParser:
    """离线假解析：确定性生成多页 Markdown + 一张示意图，用于本地联调与自测。"""

    def parse(self, pdf: bytes, filename: str) -> MineruResult:
        """不看 PDF 内容，直接按固定模板返回 3 页 Markdown 与第 2 页的一张图。"""
        stem = (filename or "document.pdf").rsplit(".pdf", 1)[0]
        size_kb = max(1, len(pdf) // 1024)
        pages = 3
        markdown_pages = [
            "\n".join(
                [
                    f"# {stem}",
                    "",
                    "<!-- page: 1 -->",
                    f"这是《{stem}》（约 {size_kb} KB）的离线 mock 解析结果，用于本地联调整条链路。",
                    "",
                    "系统架构由前端网页、后端服务、问答编排智能体、解析工作器和消息队列组成。",
                    "",
                    "<!-- page: 2 -->",
                    "解析工作器调用 MinerU 把 PDF 解析成 Markdown、版面 JSON 和图片资源，再写入向量库。",
                    "",
                    "![系统架构图](assets/figure-1.png)",
                    "",
                    "<!-- page: 3 -->",
                    "图片资源使用短时签名地址，前后端通过文档编号、页码和版面框关联；",
                    "文本切片与图片资源按同一页建立关联，问答时一起作为上下文。",
                ]
            )
        ]
        return MineruResult(
            markdown="\n".join(markdown_pages),
            pages=pages,
            images=[
                MineruImage(
                    name="figure-1.png",
                    data=placeholder_image_png(),
                    page=2,
                    bbox=[72.0, 120.0, 480.0, 360.0],
                    asset_type="figure",
                    caption="系统架构图",
                )
            ],
        )


class LocalPdfParser:
    """本地 PDF 文本解析（PyMuPDF）：真实页数与文字，没有版面/图片。

    没有配置 MINERU_API_URL 时用它兜底：比 mock 诚实——页数、正文都来自真实 PDF，
    只是拿不到版面块与插图（问答仍可用，图片相关能力降级）。
    """

    def __init__(self) -> None:
        """延迟导入可选依赖 PyMuPDF；没装时抛 ImportError，由 parser_from_config 退回 mock。"""
        try:
            import pymupdf as fitz  # noqa: PLC0415  # 新包名
        except ImportError:  # 旧安装只有 fitz 名字
            import fitz  # noqa: PLC0415

        self._fitz = fitz

    def parse(self, pdf: bytes, filename: str) -> MineruResult:
        """按页抽取文字与内嵌图片，拼成带 `<!-- page: N -->` 标记的 Markdown。"""
        stem = (filename or "document.pdf").rsplit(".pdf", 1)[0]
        document = self._fitz.open(stream=pdf, filetype="pdf")
        page_texts: list[str] = []
        images: list[MineruImage] = []
        seen_xrefs: set[int] = set()
        try:
            for page_index, page in enumerate(document, start=1):
                text = page.get_text("text").strip()
                page_texts.append(
                    text
                    or "（本页没有可提取的文字，可能是扫描图片；配置 MINERU_API_URL 可用 OCR/版面解析）"
                )
                # 版式截图：幻灯片里的架构图/流程图多是矢量绘制，位图提取不到；
                # 有图形或图片的页面整页渲染成 PNG，问答时作为该页配图（有图可看）
                try:
                    has_graphics = bool(page.get_drawings()) or bool(page.get_images(full=True))
                except Exception:
                    has_graphics = False
                if has_graphics:
                    try:
                        page_pixmap = page.get_pixmap(dpi=96)
                    except Exception:
                        page_pixmap = None
                    if page_pixmap is not None and page_pixmap.width >= 200:
                        images.append(
                            MineruImage(
                                name=f"page-{page_index}.png",
                                data=page_pixmap.tobytes("png"),
                                page=page_index,
                                bbox=[0.0, 0.0, float(page.rect.width), float(page.rect.height)],
                                asset_type="image",
                                caption=f"第 {page_index} 页版面截图",
                            )
                        )

                # 抽取页面内嵌图片（跳过小图标）：转成 PNG，页码/版面框一并带上
                for image_info in page.get_images(full=True):
                    xref = int(image_info[0])
                    if xref in seen_xrefs:
                        continue
                    seen_xrefs.add(xref)
                    try:
                        pixmap = self._fitz.Pixmap(document, xref)
                    except Exception:
                        continue
                    if pixmap.width < 60 or pixmap.height < 60:
                        continue
                    bbox = None
                    try:
                        rects = page.get_image_rects(xref)
                        if rects:
                            rect = rects[0]
                            bbox = [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)]
                    except Exception:
                        pass
                    images.append(
                        MineruImage(
                            name=f"figure-{len(images) + 1}.png",
                            data=pixmap.tobytes("png"),
                            page=page_index,
                            bbox=bbox,
                            asset_type="figure",
                            caption=f"第 {page_index} 页图片",
                        )
                    )
        finally:
            document.close()
        markdown_parts = [f"# {stem}"]
        for index, page_text in enumerate(page_texts, start=1):
            markdown_parts.append(f"<!-- page: {index} -->\n\n{page_text}")
        return MineruResult(markdown="\n\n".join(markdown_parts), pages=len(page_texts), images=images)


def parser_from_config(mineru_api_url: str = "", mineru_mock: bool | None = None, timeout_s: float = 300.0) -> MineruParser:
    """选择解析器：显式 mock > MINERU_API_URL 真实服务 > 本地 PyMuPDF 文本解析 > 离线 mock。"""
    if mineru_mock:
        return MockMineruParser()
    if mineru_api_url:
        return HttpMineruParser(mineru_api_url, timeout_s)
    try:
        return LocalPdfParser()
    except ImportError:
        # 没装 PyMuPDF 的机器仍可离线跑通链路（内容为固定 mock）
        return MockMineruParser()


def main() -> None:
    """自检：mock 产物结构、配置分支选择 mock/真实解析器。"""
    pdf = b"%PDF-1.7\n" + b"0" * 4096
    parser = MockMineruParser()
    result = parser.parse(pdf, "作业2-说明.pdf")
    print("[parse-worker.mineru_runner] 页数:", result.pages, "| 图片数:", len(result.images))
    print("[parse-worker.mineru_runner] Markdown 片段:")
    print("\n".join(result.markdown.splitlines()[:8]))
    assert result.pages == 3 and len(result.images) == 1
    assert "<!-- page: 2 -->" in result.markdown and "![系统架构图]" in result.markdown
    image = result.images[0]
    assert image.data.startswith(b"\x89PNG") and image.page == 2 and image.bbox is not None
    assert "作业2-说明" in result.markdown
    print("[parse-worker.mineru_runner] mock 占位图片字节数:", len(image.data))

    auto_parser = parser_from_config("")
    print("[parse-worker.mineru_runner] 无 MINERU_API_URL 时的解析器:", type(auto_parser).__name__)
    assert isinstance(auto_parser, (LocalPdfParser, MockMineruParser))

    explicit_mock = parser_from_config("", mineru_mock=True)
    print("[parse-worker.mineru_runner] 强制 mock 时的解析器:", type(explicit_mock).__name__)
    assert isinstance(explicit_mock, MockMineruParser)

    # 本地解析器：造一个两页 PDF，验证真实页数与文字都能取到
    try:
        try:
            import pymupdf as fitz  # noqa: PLC0415
        except ImportError:
            import fitz  # noqa: PLC0415

        # 造一个两页文字 + 一张内嵌图片的 PDF：验证真实页数、文字与图片抽取
        document = fitz.open()
        first = document.new_page()
        first.insert_text((72, 72), "Local parser page one")
        second = document.new_page()
        second.insert_text((72, 72), "Local parser page two")
        second.insert_image(fitz.Rect(72, 100, 472, 325), stream=placeholder_image_png())
        pdf_bytes = document.tobytes()
        document.close()
        local = LocalPdfParser()
        local_result = local.parse(pdf_bytes, "本地解析测试.pdf")
        print("[parse-worker.mineru_runner] 本地解析页数:", local_result.pages, "| 图片数:", len(local_result.images))
        assert local_result.pages == 2 and "page two" in local_result.markdown
        assert local_result.markdown.count("<!-- page:") == 2
        assert local_result.images, "应抽取出页面截图/内嵌图片"
        assert all(image.data.startswith(b"\x89PNG") for image in local_result.images)
        assert any(image.asset_type == "image" for image in local_result.images), "有图形的页面应有版面截图"
        assert any(image.asset_type == "figure" and image.page == 2 for image in local_result.images), "应抽取出内嵌图片"
    except ImportError:
        print("[parse-worker.mineru_runner] 未安装 PyMuPDF，跳过本地解析分支检查")
    auto_http = parser_from_config("http://mineru:8000")
    print("[parse-worker.mineru_runner] 配置 MINERU_API_URL 时的解析器:", type(auto_http).__name__)
    assert isinstance(auto_http, HttpMineruParser)
    forced_mock = parser_from_config("http://mineru:8000", mineru_mock=True)
    assert isinstance(forced_mock, MockMineruParser)
    print("[parse-worker.mineru_runner] 自检通过")


if __name__ == "__main__":
    main()
