import os
from pageindex import PageIndexClient

# ============================================================================
# Philip 版：官方 PageIndex SDK Local 模式 + 同目录 .env 自动加载
# 设计原则：最小侵入 / 路径健壮（SCRIPT_DIR 绝对定位）/ 先省费验证后再构建
# 依赖: pip install -U pageindex litellm python-dotenv（当前主环境已全部装好 ✅）
# ============================================================================

import sys
import time
import json
import argparse
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(SCRIPT_DIR)

ENV_PATH = os.path.join(SCRIPT_DIR, ".env")
STORAGE_PATH = os.path.join(SCRIPT_DIR, ".pageindex")
DEFAULT_PDF = os.path.join(SCRIPT_DIR, "资料", "2404.16130v2-GraphRAG.pdf")
REUSE_DOC_ID_DEFAULT = "pi-bb831222ab834527829560b6d7a468bc"   # 已构建好的 GraphRAG 26 页论文索引，省 $0.026 构建费


def load_env_and_validate():
    """加载 .env，按 LLM_PROVIDER 把 DeepSeek/Qwen key 显式塞进 os.environ，供 LiteLLM 读"""
    if not os.path.isfile(ENV_PATH):
        raise FileNotFoundError(
            f"找不到 .env：{ENV_PATH}\n请在 Week7/Week07 目录下建 .env，模板示例：\n"
            "LLM_PROVIDER=deepseek\nDEEPSEEK_API_KEY=sk-xxx\nDEEPSEEK_BASE_URL=https://api.deepseek.com/v1\n"
            "DEEPSEEK_MODEL=deepseek-v4-flash\n"
        )
    load_dotenv(ENV_PATH, override=False)

    provider = os.environ.get("LLM_PROVIDER", "deepseek").strip().lower()
    if provider not in ("deepseek", "qwen"):
        raise ValueError(f"LLM_PROVIDER={provider!r} 不受支持，仅支持 'deepseek' / 'qwen'")

    cfg_map = {
        "deepseek": {
            "key_var": "DEEPSEEK_API_KEY",
            "base_var": "DEEPSEEK_BASE_URL",
            "model_var": "DEEPSEEK_MODEL",
            "prefix": "deepseek/",
            "default_index": "deepseek-v4-flash",
            "default_chat": "deepseek-v4-pro",
        },
        "qwen": {
            "key_var": "QWEN_API_KEY",
            "base_var": "QWEN_BASE_URL",
            "model_var": "QWEN_MODEL",
            "prefix": "qwen/",
            "default_index": "qwen-plus",
            "default_chat": "qwen-max",
        },
    }
    cfg = cfg_map[provider]

    api_key = os.environ.get(cfg["key_var"], "").strip()
    base_url = os.environ.get(cfg["base_var"], "").strip().rstrip("/")

    # 安全校验：禁止占位符 key
    if not api_key or api_key.startswith("你的_") or api_key.lower().startswith("sk-xxx"):
        raise RuntimeError(
            f".env 中 {cfg['key_var']} 为空或仍是占位符，请填入真实 API Key 后再运行。\n"
            f"（LiteLLM 看到供应商前缀 {cfg['prefix'][:-1]!r} 会自动去读 {cfg['key_var']} 这个 env var）"
        )

    # LiteLLM 要求这些 env var 必须在 os.environ 里存在（即使 dotenv 已经加载了，保险再 setdefault 一遍）
    os.environ.setdefault(cfg["key_var"], api_key)
    if base_url:
        base_env = {
            "deepseek": "DEEPSEEK_API_BASE",
            "qwen": "QWEN_BASE_URL",
        }[provider]
        os.environ.setdefault(base_env, base_url)

    # 索引模型：便宜的 Flash/Plus（README 说 index 用基础模型就够）
    index_model = cfg["prefix"] + cfg["default_index"]
    # 问答模型：用户指定的默认模型，没指定就回退到 Pro/Max（更准但略贵）
    chat_model = cfg["prefix"] + (
        (os.environ.get(cfg["model_var"], "").strip() or cfg["default_chat"])
    )

    print("=" * 68)
    print(f"[Env] 加载 .env        : {ENV_PATH}")
    print(f"      LLM_PROVIDER      : {provider}")
    print(f"      Index 模型（构建）: {index_model}（便宜模型做摘要 OK）")
    print(f"      Chat  模型（问答）: {chat_model}（用好模型翻树推理）")
    print(f"      .pageindex 存储   : {STORAGE_PATH}")
    if os.path.isfile(DEFAULT_PDF):
        from pathlib import Path
        size_mb = Path(DEFAULT_PDF).stat().st_size / 1024 / 1024
        print(f"      默认测试 PDF      : {DEFAULT_PDF} （{size_mb:.2f} MB）")
    else:
        print(f"      ⚠️ 默认 PDF 不存在: {DEFAULT_PDF}")
    print("=" * 68)
    return index_model, chat_model, provider


def print_tree_structure(node, indent: int = 0):
    """递归打印 get_document_structure() 返回到的分层 tree.json 摘要"""
    pad = "  " * indent
    title = (node or {}).get("title") or (node or {}).get("name") or "(untitled)"
    page_ref = (node or {}).get("page_reference") or (node or {}).get("pages") or ""
    if page_ref and isinstance(page_ref, list):
        page_ref = f"p.{min(page_ref)}-{max(page_ref)}" if page_ref else ""
    elif isinstance(page_ref, str):
        pass
    else:
        page_ref = ""
    summary = ((node or {}).get("summary") or "").strip().replace("\n", " ")
    if len(summary) > 70:
        summary = summary[:67] + "…"
    out = f"{pad}├─ {title}"
    if page_ref:
        out += f"  [{page_ref}]"
    if summary:
        out += f"  :: {summary}"
    print(out)
    children = (node or {}).get("children") or []
    if children:
        for ch in children:
            print_tree_structure(ch, indent + 1)


def list_documents(_client=None) -> list:
    """列出本地 .pageindex 下已经构建好的文档清单（读取 manifest.json 官方格式 {"docs": {id: info}}）"""
    manifest_path = os.path.join(STORAGE_PATH, "manifest.json")
    rows = []
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                mani = json.load(f)
            docs_dict = mani.get("docs", {}) or {}
            for did, info in docs_dict.items():
                if isinstance(info, dict):
                    name = info.get("name") or info.get("file_name") or "(未命名)"
                    status = info.get("status") or "unknown"
                    npages = info.get("pageNum") or info.get("page_count") or info.get("num_pages") or ""
                    created = info.get("createdAt") or info.get("created_at") or info.get("updated_at") or ""
                    mode = info.get("mode") or info.get("index_mode") or ""
                    rows.append((did, name, status, npages, created, mode))
        except Exception as e:
            print(f"⚠️  解析 manifest.json 失败：{e}")
    return rows


def ensure_pdf_exists(path: str):
    if not os.path.isfile(path):
        raise FileNotFoundError(f"PDF 不存在，无法构建：{path}")
    from pathlib import Path
    size_mb = Path(path).stat().st_size / 1024 / 1024
    n_pages_hint = 26 if "GraphRAG" in Path(path).name else "?"
    print(f"\n📄 准备构建：{path} ({size_mb:.2f} MB，约 {n_pages_hint} 页)")
    print(
        "   💰 官方参考成本 ≈ $0.0011 / 页（gpt-5.6-luna 基准）→ "
        f"26 页 ≈ ${26*0.0011:.3f} ≈ ¥{26*0.0011*7.2:.2f}，实际用 DeepSeek Flash 更便宜"
    )
    print("   ⏱  预期耗时：13s(9页) → 4.5min(1098页)，26 页 ≈ 30-60 秒\n")


def submit_and_wait(client, pdf_path: str) -> str:
    """submit_document 同步构建（wait=True），打印进度条感的日志"""
    ensure_pdf_exists(pdf_path)
    t0 = time.time()
    print("🔨 开始构建（Flash 索引模式，默认）...")
    result = client.submit_document(pdf_path, wait=True)
    doc_id = result.get("doc_id") or result.get("id")
    dt = time.time() - t0
    status = result.get("status", "?")
    if not doc_id:
        raise RuntimeError(f"submit_document 返回结果没有 doc_id：{result!r}")
    print(f"✅ 构建完成  status={status}  doc_id={doc_id}  耗时 {dt:.1f}s")
    # 尝试打印结构
    try:
        struct = client.get_document_structure(doc_id)
        root = struct if isinstance(struct, dict) else (struct[0] if isinstance(struct, list) and struct else {})
        print("🌳 分层 PageIndex 树结构摘要：")
        print_tree_structure(root, indent=0)
    except Exception as e:
        print(f"⚠️  读树结构失败（不影响后续问答）：{e}")
    return doc_id


BENCH_QUESTIONS = [
    "1) PageIndex 与传统 Vector RAG 的核心区别是什么？（用 3 点概括）",
    "2) GraphRAG 的工作流程分为哪几个关键步骤？请按先后顺序列出。",
    "3) 本文 GraphRAG 实验对比了哪些 baseline 方法？在什么指标上 GraphRAG 显著领先？",
]


def run_chat_loop(client, doc_id: str, one_shot_question: str | None = None):
    """问答：one_shot_question→单问题退出；None→交互式 CLI"""
    print(f"\n💬 已绑定 doc_id = {doc_id}")
    print("   输入任意问题回车回答；输入 q/quit/exit/空 退出")
    print("─" * 68)

    def ask(q: str):
        t0 = time.time()
        try:
            ans = client.chat(q, doc_id=doc_id)
            dt = time.time() - t0
            text = ans if isinstance(ans, str) else (
                (ans or {}).get("answer")
                or (ans or {}).get("content")
                or (ans or {}).get("message", {}).get("content")
                or str(ans)
            )
            print(f"\n🤖 回答 （{dt:.1f}s）：")
            print(text)
        except Exception as e:
            print(f"\n❌ 调用失败：{e}")
            dt = 0.0
        print("─" * 68)

    if one_shot_question:
        print(f"\n🙋 单问题模式 > {one_shot_question}")
        ask(one_shot_question)
        return

    print("\n🎯 先跑 3 道省费验证题（复用现有树，不花构建费）：")
    for q in BENCH_QUESTIONS:
        print(f"\n🙋 {q}")
        ask(q.split(")", 1)[-1].strip() if ")" in q[:3] else q)
        time.sleep(0.5)

    while True:
        try:
            user_q = input("\n🙋 请输入问题（q 退出）> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 已退出")
            break
        if not user_q or user_q.lower() in {"q", "quit", "exit"}:
            print("👋 已退出")
            break
        ask(user_q)


def main():
    index_model, chat_model, _provider = load_env_and_validate()

    parser = argparse.ArgumentParser(description="Philip版：官方 PageIndex SDK Local 模式（.env 自动加载）")
    parser.add_argument("--list", action="store_true", help="仅列出本地已构建的文档清单")
    parser.add_argument("--submit", action="store_true", help=f"重新构建默认 PDF：{DEFAULT_PDF}（会产生少量 LLM 构建费）")
    parser.add_argument("--pdf", default=DEFAULT_PDF, help=f"自定义构建 PDF 路径（默认：{DEFAULT_PDF}）")
    parser.add_argument("--doc-id", default=None, help=f"手动指定 doc_id 问答（默认复用已构建的 {REUSE_DOC_ID_DEFAULT[:12]}…，省构建费）")
    parser.add_argument("--question", default=None, help="单问题非交互模式（答完就退出）")
    args = parser.parse_args()

    # Local 模式：不传 api_key → PageIndexClient 内部自动走 LocalAPI
    # ⚠️ PageIndex 0.2.14 client.py L391 强约束：storage_path 与 index= 参数互斥，不能同时传
    #    我们脚本顶部已经 os.chdir(SCRIPT_DIR)，所以 ./.pageindex 默认就是 SCRIPT_DIR/.pageindex，符合路径要求
    client = PageIndexClient(
        index=index_model,
        chat=chat_model,
    )

    # 打印已构建清单（任何模式都先显示，让用户知道本地有哪些可复用）
    docs = list_documents(client)
    print(f"\n📚 本地已构建文档数：{len(docs)}")
    for i, row in enumerate(docs, 1):
        did, name, status, npages, created, mode = row
        mark = "⭐ 默认复用" if did == REUSE_DOC_ID_DEFAULT else " "
        mode_str = f"mode={mode}" if mode else ""
        print(
            f"   {i:>2}. [{status:8s}] {did}  {name!r}  页数={npages}  {created or ''}  {mode_str}  {mark}"
        )
    if not docs:
        print("   （空，第一次运行请加 --submit 构建）")

    if args.list:
        return

    # 分支 1：重新构建
    if args.submit:
        doc_id = submit_and_wait(client, args.pdf)
        run_chat_loop(client, doc_id, one_shot_question=args.question)
        return

    # 分支 2：问答（复用指定 doc_id 或默认）
    doc_id = args.doc_id or REUSE_DOC_ID_DEFAULT
    # 校验 doc_id 真实存在（避免用户乱输）
    if docs and doc_id not in {d[0] for d in docs}:
        print(f"⚠️  doc_id={doc_id!r} 不在本地已构建清单中。请：")
        print(f"   (a) 从上面清单里选一个，用 --doc-id 指定；")
        print(f"   (b) 或加 --submit 构建新 PDF；")
        print(f"   (c) 如果你确认云端/其他存储有这个 doc_id，忽略此警告继续运行。\n")
        # 不直接死，让用户决定要不要继续（容错）
    run_chat_loop(client, doc_id, one_shot_question=args.question)


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"\n❌ {type(e).__name__}: {e}")
        sys.exit(2)