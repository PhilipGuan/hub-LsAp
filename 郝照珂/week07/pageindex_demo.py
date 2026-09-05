"""第七周作业：PageIndex 本地索引构建、查看目录树和文档问答。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pageindex import PageIndexClient


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_PDF = (
    BASE_DIR.parent / "Week07" / "Week07" / "资料" / "2404.16130v2-GraphRAG.pdf"
)
DEFAULT_STORAGE = BASE_DIR / ".pageindex"
DEFAULT_OUTPUT = BASE_DIR / "outputs"


def configure_console() -> None:
    """Windows 默认 GBK 终端无法显示部分 PDF Unicode 字符。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="用 PageIndex 为本地 PDF 构建树索引并问答"
    )
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF, help="PDF 路径")
    parser.add_argument("--question", default="这篇论文提出的 GraphRAG 方法是什么？")
    parser.add_argument("--doc-id", help="复用已构建文档；不填则先构建索引")
    parser.add_argument("--storage", type=Path, default=DEFAULT_STORAGE)
    parser.add_argument(
        "--index-model",
        default=os.getenv("PAGEINDEX_INDEX_MODEL", "deepseek/deepseek-v4-pro"),
    )
    parser.add_argument(
        "--chat-model",
        default=os.getenv("PAGEINDEX_CHAT_MODEL", "deepseek/deepseek-v4-pro"),
    )
    parser.add_argument(
        "--api-base",
        default=os.getenv("PAGEINDEX_API_BASE", ""),
        help="Ollama/OpenAI 兼容端点；云厂商模型可传空字符串",
    )
    parser.add_argument(
        "--mode",
        choices=("flash", "standard"),
        default="flash",
        help="flash 从 PDF 布局抽取树；standard 由 LLM 完整构树",
    )
    parser.add_argument("--show-tree", action="store_true", help="把目录树打印到终端")
    parser.add_argument("--no-chat", action="store_true", help="只构建索引，不问答")
    return parser.parse_args()


def make_client(args: argparse.Namespace) -> PageIndexClient:
    backend: dict[str, Any] | None = None
    if args.api_base:
        # index_backend 使用 LiteLLM 的 api_base；chat_backend 会由 SDK
        # 自动把 api_base 归一化成 base_url。
        backend = {"api_base": args.api_base}

    return PageIndexClient(
        index_model=args.index_model,
        chat_model=args.chat_model,
        storage_path=str(args.storage.resolve()),
        index_backend=backend,
        chat_backend=backend,
    )


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    configure_console()
    load_dotenv(BASE_DIR / "API配置.env")
    args = parse_args()
    pdf = args.pdf.expanduser().resolve()
    storage = args.storage.expanduser().resolve()

    if not pdf.is_file() and not args.doc_id:
        print(f"错误：找不到 PDF：{pdf}", file=sys.stderr)
        return 2

    uses_deepseek = args.index_model.startswith("deepseek/") or args.chat_model.startswith(
        "deepseek/"
    )
    if uses_deepseek and not os.getenv("DEEPSEEK_API_KEY", "").strip():
        print(
            f"错误：尚未填写 DeepSeek API Key。\n"
            f"请打开 {BASE_DIR / 'API配置.env'}，填写 DEEPSEEK_API_KEY 后重试。",
            file=sys.stderr,
        )
        return 2

    print(f"PageIndex storage : {storage}")
    print(f"Index model       : {args.index_model}")
    print(f"Chat model        : {args.chat_model}")
    client = make_client(args)

    if args.doc_id:
        doc_id = args.doc_id
        print(f"复用文档          : {doc_id}")
    else:
        print(f"开始构建索引      : {pdf}")
        result = client.submit_document(str(pdf), mode=args.mode, wait=True)
        doc_id = result["doc_id"]
        print(f"索引构建完成      : {doc_id}")

    metadata = client.get_document(doc_id)
    tree = client.get_document_structure(doc_id)
    write_json(DEFAULT_OUTPUT / "document.json", metadata)
    write_json(DEFAULT_OUTPUT / "tree.json", tree)
    (DEFAULT_OUTPUT / "doc_id.txt").write_text(doc_id + "\n", encoding="utf-8")

    if args.show_tree:
        print("\n===== Tree Index =====")
        print(json.dumps(tree, ensure_ascii=False, indent=2))

    if not args.no_chat:
        print("\n===== Question =====")
        print(args.question)
        print("\n===== Answer =====")
        answer = client.chat(args.question, doc_id=doc_id)
        print(answer)
        (DEFAULT_OUTPUT / "answer.md").write_text(
            f"# PageIndex 问答结果\n\n"
            f"- doc_id: `{doc_id}`\n"
            f"- question: {args.question}\n\n"
            f"## Answer\n\n{answer}\n",
            encoding="utf-8",
        )

    print(f"\n结果已写入        : {DEFAULT_OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
