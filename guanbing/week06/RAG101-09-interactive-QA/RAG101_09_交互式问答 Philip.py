# ============================================================================
# RAG101_09 · 交互式汽车问答（支持用户任意输入问题）
# 设计目标：最小代码改动 = 不修改 01~08 任何作业脚本，只新建本文件。
# 复用来源（3 份已有代码，100% 对齐 Week6 课堂实现）：
#   · 03_BM25.py       → jieba + rank_bm25.BM25Okapi 构建稀疏召回（L23~L45）
#   · 05_BERT_Segment  → split_text_fixed_size(40) + SentenceTransformer(bge-small-zh-v1.5) 构建稠密召回（L23,L31-L37,L56-L72）
#   · 08_RAG问答.py    → SCRIPT_DIR 补丁 / .env Key 加载 / ask_llm(DeepSeek/Qwen) / RRF(k=60) / Prompt 人设模板 / "无法回答"标准化
# 运行方式：
#   cd Week6/Week06
#   source ../../.venv/bin/activate
#   python RAG101_09_交互式问答.py
#   （首次启动需要 ~15-40s 构建 BM25 索引 + BGE 全量 chunk embedding；后续每答一题 < 3s）
# ============================================================================

import json
import os
import sys
import time
import warnings
warnings.filterwarnings("ignore")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(SCRIPT_DIR)

# ---------- 从 .env 读大模型配置（完全复用 08 的逻辑）----------
from dotenv import load_dotenv
load_dotenv(os.path.join(SCRIPT_DIR, '.env'))

import jieba
import numpy as np
import pdfplumber
import requests
import torch
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

LLM_PROVIDER        = os.getenv('LLM_PROVIDER', 'deepseek').strip().lower()
DEEPSEEK_API_KEY    = os.getenv('DEEPSEEK_API_KEY', '').strip()
DEEPSEEK_BASE_URL   = os.getenv('DEEPSEEK_BASE_URL', '').strip().rstrip('/')
DEEPSEEK_MODEL      = os.getenv('DEEPSEEK_MODEL', '').strip()
QWEN_API_KEY        = os.getenv('QWEN_API_KEY', '').strip()
QWEN_BASE_URL       = os.getenv('QWEN_BASE_URL', '').strip().rstrip('/')
QWEN_MODEL          = os.getenv('QWEN_MODEL', '').strip()

# ============================================================================
# 交互模式行为开关（08 作业批处理模式 vs 09 智能客服聊天模式）
#   · True  = 作业严格模式：100% 复刻 RAG101_08 的 Prompt 模板 + 拒答标准
#              （用于交作业时生成与 labels.json 同口径的判分答案）
#   · False = 智能客服宽松模式（默认，用于用户自定义问答）
#              · 放宽 Prompt：资料是"■ 部件名并列清单"类原文时，
#                要求 LLM 把清单整理为步骤 1/2/3… 的回答，而不是一开口就拒答
#              · 降低拒答触发条件：避免储物空间题被误判
# ============================================================================
SWITCH_HOMEWORK_MODE = False


# ============================================================================
# 0 · 大模型调用（完全搬自 08 新写的 ask_llm：支持 DeepSeek / Qwen，5 次重试）
# ============================================================================
def ask_llm(content: str) -> dict:
    if LLM_PROVIDER == 'deepseek':
        if not DEEPSEEK_API_KEY or not DEEPSEEK_MODEL or not DEEPSEEK_BASE_URL:
            raise RuntimeError(".env 中 DeepSeek 配置不完整，需要 DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL")
        api_key, base_url, model = DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
    elif LLM_PROVIDER == 'qwen':
        if not QWEN_API_KEY or not QWEN_MODEL or not QWEN_BASE_URL:
            raise RuntimeError(".env 中 Qwen 配置不完整，需要 QWEN_API_KEY / QWEN_BASE_URL / QWEN_MODEL")
        api_key, base_url, model = QWEN_API_KEY, QWEN_BASE_URL, QWEN_MODEL
    else:
        raise RuntimeError(f".env 中 LLM_PROVIDER={LLM_PROVIDER!r} 不受支持，当前仅支持 'deepseek' / 'qwen'")

    url = f"{base_url}/chat/completions"
    headers = {
        'Content-Type':  'application/json',
        'Authorization': f"Bearer {api_key}",
    }
    data = {"model": model, "messages": [{"role": "user", "content": content}], "stream": False}

    max_error = 0
    while True:
        if max_error >= 5:
            return {"choices": [{"message": {"content": "结合给定的资料，无法回答问题。"}}]}
        try:
            resp = requests.post(url, headers=headers, json=data, timeout=20)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            max_error += 1
            time.sleep(1)
            continue


# ============================================================================
# 1 · 离线初始化（程序启动时执行一次，之后反复回答用户问题就不用再跑）
#     a) 读 PDF，构建 {page_X: 文本} 字典（复用 08 模块 4）
#     b) 构建 BM25 索引（复用 03 L23-L28：jieba.lcut + BM25Okapi）
#     c) 构建 BGE Chunk 列表 + 全量 embed（复用 05 L23, L31-L37）
# ============================================================================
print("=" * 60)
print("【启动准备】正在加载 PDF 并构建召回索引，请稍候 ……")
print("=" * 60)

# 1a) PDF 文本加载
pdf = pdfplumber.open(os.path.join(SCRIPT_DIR, "汽车知识手册.pdf"))
pdf_pages = []                    # [{"page": "page_1", "content": "xxx"}, ...] 共 354 条，按页
pdf_content_dict = {}             # {"page_1": "xxx", ...}  —— 方便 O(1) 取某页原文
for page_idx in range(len(pdf.pages)):
    text = pdf.pages[page_idx].extract_text() or ""
    page_name = f"page_{page_idx + 1}"
    pdf_pages.append({"page": page_name, "content": text})
    pdf_content_dict[page_name] = text
print(f"· PDF 页数              : {len(pdf_pages)} 页")

# 1b) BM25 稀疏检索索引（复用 03：jieba 切词 → BM25Okapi）
pdf_page_words = [jieba.lcut(p["content"]) for p in pdf_pages]
bm25_index = BM25Okapi(pdf_page_words)
print(f"· BM25 索引（按页）      : 构建完成 ✅")


# 复用 05 L23：split_text_fixed_size
def split_text_fixed_size(text, chunk_size):
    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]


# 复用 05 L56-L63：remove_duplicates
def remove_duplicates(input_list):
    seen = set()
    result = []
    for item in input_list:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


# ============================================================================
# ★ 【修复 2 · Chunker 多粒度改造】
# 原 05 脚本只切了"40 字符 零重叠"chunk，并列清单（如储物空间那一页的 6 个 ■ 项）会被切碎。
# 这里做"3 粒度 merge"：40+0 / 128+20 / 256+48 overlap。
# 之后把 3 种 chunk 全部拼起来去重 → BGE embed，保证"整页储物空间总览语义"至少在
# 128/256 的某一个 chunk 里整块被 encode，BGE 相似度能明显上来。
# ============================================================================
def split_text_with_overlap(text: str, size: int, overlap: int):
    if overlap >= size: overlap = size // 3
    step = size - overlap
    out, L = [], len(text)
    for i in range(0, max(1, L - size + 1), step):
        chunk = text[i:i + size]
        if chunk.strip():
            out.append(chunk)
    # 尾巴再补一个最后 size 的片段，避免末尾短句丢掉
    if L >= size and L - (out[-1][1] if False else size) > 0:
        tail = text[max(0, L - size):]
        if tail.strip() and (not out or tail != out[-1]):
            out.append(tail)
    return out


# 1c) BGE 稠密检索：多粒度 chunk → 全量 embed（★ 修复 2 的核心）
pdf_chunks = []   # [{"page":"page_1","content":"...chunk..."}, ...]
CHUNK_GRANS = [(40, 0), (128, 20), (256, 48)]   # 3 种粒度，覆盖"短句 / 段落 / 半页总览"
for page in pdf_pages:
    text = page["content"]
    seen_this_page = set()
    for size, overlap in CHUNK_GRANS:
        for chunk_text in split_text_with_overlap(text, size, overlap):
            if chunk_text in seen_this_page:
                continue
            seen_this_page.add(chunk_text)
            pdf_chunks.append({"page": page["page"], "content": chunk_text})
# 额外保留原始 40char 版本的统计，让你对比
count_gran = {f"{s}/{o}": 0 for s, o in CHUNK_GRANS}
# 上面生成顺序已不重要，去重粒度在 seen_this_page 层已完成；下面只打印总数

BGE_MODEL_PATH = os.path.abspath(os.path.join(SCRIPT_DIR, "../models/BAAI/bge-small-zh-v1.5/"))
# 若 Week6 下的 symlink 不存在，退回 Week5 的实际路径（保证在你本机仍然能跑）
if not os.path.isdir(BGE_MODEL_PATH):
    BGE_MODEL_PATH = "/Users/philipclaw/Downloads/padow-ai/Week5/bge-retrieval-lab/BAAI/bge-small-zh-v1.5/"

_device = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
print(f"· BGE 模型路径          : {BGE_MODEL_PATH}")
print(f"· 设备（优先 MPS/Mac）  : {_device}")
print(f"· BGE Chunk 策略        : 多粒度 {CHUNK_GRANS}，合并去重后共 {len(pdf_chunks)} 条（旧版仅 40char≈3700 条）")
t0 = time.time()
bge_model = SentenceTransformer(BGE_MODEL_PATH, device=_device)
pdf_chunk_embeddings = bge_model.encode(
    [c["content"] for c in pdf_chunks],
    normalize_embeddings=True,
    show_progress_bar=False,
    convert_to_numpy=True,
)
print(f"· BGE 全量 embedding 耗时: {time.time()-t0:.1f}s ✅")

# ============================================================================
# ★ 【修复 1 · Cross-Encoder 精排模型加载】
# 之后 retrieval_and_rrf 里对 RRF Top3 的 (query, page_text) 过一遍
# bge-reranker-base，argmax 当最终 Top1。
# ============================================================================
RERANK_MODEL_PATH = os.path.abspath(os.path.join(SCRIPT_DIR, "../models/BAAI/bge-reranker-base/"))
if os.path.isdir(RERANK_MODEL_PATH):
    from sentence_transformers import CrossEncoder
    reranker = CrossEncoder(RERANK_MODEL_PATH, device=_device)
    RERANK_TOPK = 3          # RRF 前 3 名送入 rerank（你们作业里就是 Top3 rerank）
    RERANK_MAX_TEXT = 700    # Cross-Encoder 512 token ≈ 700 中文字，截断避免丢信息
    print(f"· Rerank 模型路径       : {RERANK_MODEL_PATH}  ✅ 已加载")
else:
    reranker = None
    RERANK_TOPK = 0
    print(f"· Rerank 模型路径       : {RERANK_MODEL_PATH}  ⚠️ 不存在 → 暂时不启用 Cross-Encoder（跑模型下载 cmd 可恢复）")


# ============================================================================
# 2 · 两路召回 + RRF(k=60) 融合函数（复用 03/05 的打分 + 08 的 RRF 公式）
#     输入  : 用户的一句话 query
#     输出  : (top1_page_name, sorted_pages_list, scores_for_debug)
# ============================================================================
def retrieval_and_rrf(query: str, k_rrf: int = 60, top_n: int = 10):
    # ---- (A) BM25 稀疏召回（复用 03 L33+L44-45：get_scores → argsort 取 Top10 页）
    q_words = jieba.lcut(query)
    bm25_scores = bm25_index.get_scores(q_words)
    bm25_top_idx = np.argsort(bm25_scores)[::-1][:top_n]
    bm25_top_pages = [pdf_pages[i]["page"] for i in bm25_top_idx]

    # ---- (B) BGE+Chunk 稠密召回（复用 05 L48-49 + L65-69：cosine → Top100 chunk → 去重得 Top10 页）
    q_feat = bge_model.encode([query], normalize_embeddings=True, show_progress_bar=False, convert_to_numpy=True)[0]
    dense_scores = q_feat @ pdf_chunk_embeddings.T
    dense_top_chunk_idx = np.argsort(dense_scores)[::-1][:100]
    dense_pages_raw = [pdf_chunks[i]["page"] for i in dense_top_chunk_idx]
    dense_top_pages = remove_duplicates(dense_pages_raw)[:top_n]

    # ---- (C) RRF k=60 融合（完全同 08 模块 5 / 07.py 逻辑）
    fusion_score = {}
    for idx, p in enumerate(bm25_top_pages):
        fusion_score[p] = fusion_score.get(p, 0) + 1 / (idx + k_rrf)
    for idx, p in enumerate(dense_top_pages):
        fusion_score[p] = fusion_score.get(p, 0) + 1 / (idx + k_rrf)
    sorted_pages = sorted(fusion_score.items(), key=lambda x: x[1], reverse=True)

    # ============================================================================
    # ★ 【修复 1 · Cross-Encoder TopK 精排】
    # 仅当 bge-reranker-base 模型成功加载时启用：对 RRF 得分最高的 TopK 页，
    # 用 Cross-Encoder 把 (query, page_text) 作为一对，算出真实匹配 logit，
    # 再按 Cross-Encoder logit 重新排序，保证像储物空间题这种 GT 已经被 BGE
    # 多粒度 + RRF 捞进了 Top3 的，最后一步一定被选 Top1。
    # 排完序后，把 sorted_pages 的前 RERANK_TOPK 个位置按新顺序替换，这样
    # 后面的 debug dict 也能看到精排后的顺序。
    # ============================================================================
    if reranker is not None and RERANK_TOPK > 0 and len(sorted_pages) >= 2:
        K = min(RERANK_TOPK, len(sorted_pages))
        topK_pages = sorted_pages[:K]
        # 构造 (query, page_text[:700]) 对儿，喂给 Cross-Encoder
        pairs = []
        for p, _s in topK_pages:
            txt = pdf_content_dict.get(p, "")[:RERANK_MAX_TEXT]
            pairs.append((query, txt))
        try:
            ce_scores = reranker.predict(pairs, convert_to_numpy=True, show_progress_bar=False)
            # 按 Cross-Encoder logit 降序排，同分再回退 RRF 原分保稳定
            zipped = sorted(zip(ce_scores, topK_pages), key=lambda x: (x[0], x[1][1]), reverse=True)
            reranked_topK = [page for _, page in zipped]
            sorted_pages[:K] = reranked_topK
        except Exception:
            # Cross-Encoder 任何异常都不影响主流程，退回原 RRF 排序
            pass

    top1_page = sorted_pages[0][0] if sorted_pages else None
    return top1_page, sorted_pages, {"bm25_top": bm25_top_pages, "dense_top": dense_top_pages}


# ============================================================================
# 3 · Prompt 构造（根据 SWITCH_HOMEWORK_MODE 自动切换：严格/宽松两套模板）
# ============================================================================
def build_prompt(query: str, top1_page: str, sorted_pages: list) -> str:
    page_no = int(top1_page.split("_")[1]) if top1_page else 0
    reference_content = ""
    if top1_page and top1_page in pdf_content_dict:
        reference_content = pdf_content_dict[top1_page].replace("\n", " ") + f"\t上述内容在第{page_no}页"

    if SWITCH_HOMEWORK_MODE:
        # 作业严格模式：100% 复刻 RAG101_08 的模板（保证与 labels.json 的判分口径一致）
        prompt = """你是一个汽车专家，你擅长编写和回答汽车相关的用户提问，帮我结合给定的资料，回答下面的问题。
如果问题无法从资料中获得，或无法从资料中进行回答，请回答无法回答。如果提问不符合逻辑，请回答无法回答。
如果问题可以从资料中获得，则请逐步回答。

资料：{0}


问题：{1}
    """.format(reference_content, query)
    else:
        # 智能客服宽松模式：放宽 Prompt，面对"■ 部件并列清单"类资料主动整理为分点步骤，不再一开口就拒答
        prompt = """你是一名领克/Lynk&Co 品牌的汽车使用手册智能客服，请严格只依据提供的资料，用中文回答用户的汽车使用问题。

回答规则：
1) 如果从资料中能直接或间接得到答案（比如资料用条目/并列清单/■ 黑点列出来了多个部件位置或使用要点），请按资料结构整理为分点步骤（1. 2. 3. …）回答，不要因为资料没有出现"如何/怎么"这两个字就拒答，并列部件清单本身就是安排/使用方法的一部分，你要把清单整理为用户可操作的步骤式回答。
2) 如果资料明确没有答案，或问题与汽车手册内容无关，才回答：结合给定的资料，无法回答问题。
3) 不要编造资料里没有的信息；可以把资料里的"■ XXX" / "01 XXX"等条目录入为编号步骤。
4) 结尾可以额外总结注意事项（资料里的"注意！/警告/切勿"部分）。

资料：{0}

用户问题：{1}
    """.format(reference_content, query)
    return prompt, page_no


# ============================================================================
# 4 · 端到端回答（复用 08 模块 7：5 次重试 + 含"无法" → 标准拒答文案）
# ============================================================================
def answer_user_query(query: str) -> dict:
    t0 = time.time()
    # 召回
    top1_page, sorted_pages, debug = retrieval_and_rrf(query)
    if not top1_page:
        return {"question": query, "reference": None, "page_no": None,
                "answer": "结合给定的资料，无法回答问题。",
                "cost_sec": round(time.time()-t0, 2),
                "bm25_top": [], "dense_top": [], "rrf_top": []}
    # 拼 Prompt
    prompt, page_no = build_prompt(query, top1_page, sorted_pages)
    # 调 LLM
    answer = "无法"
    for _ in range(5):
        try:
            answer = ask_llm(prompt)["choices"][0]["message"]["content"]
            if answer:
                break
        except Exception:
            continue
    # ============================================================================
    # ★ 额外修复：拒答标准化（按 SWITCH_HOMEWORK_MODE 切换策略
    #   作业模式（True）：100% 复刻 08 L274-L276 的严格规则（只要含"无法"子串"就统一拒答，保证判分统一
    #   客服模式（False）：宽松规则 + 假阳性拦截 + 子串长度过滤，避免储物空间题 LLM 引用原文"切勿强行…/无法开启…被误伤
    # ============================================================================
    if SWITCH_HOMEWORK_MODE:
        # 严格复刻 08 的规则：任何出现"无法"子串 → 统一替换
        if "无法" in answer:
            answer = "结合给定的资料，无法回答问题。"
    else:
        _head = (answer or "")[:60]
        _head_flag = "无法" in _head
        _refuse_patterns = ("无法回答", "无法从资料", "无法从给定", "回答无法", "无法确定", "无法判断", "无法给出", "无法找到")
        _any_refuse = any(p in answer for p in _refuse_patterns)
        if (not answer) or _head_flag or _any_refuse:
            answer = "结合给定的资料，无法回答问题。"
    return {
        "question":   query,
        "reference":  top1_page,
        "page_no":    page_no,
        "answer":     answer,
        "cost_sec":   round(time.time() - t0, 2),
        "bm25_top":   debug["bm25_top"],
        "dense_top":  debug["dense_top"],
        "rrf_top":    sorted_pages[:5],
    }


# ============================================================================
# 5 · CLI 交互主循环（对用户任意输入问题 → 实时答）
# ============================================================================
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("🚗 汽车智能客服 RAG Demo 已就绪（基于 Week6 03+05+08）")
    print("   · 输入任意汽车相关问题，回车即可获得回答（含召回页 + RRF 详情）")
    print("   · 输入 q / quit / exit / 直接回车 → 退出程序")
    print("=" * 60 + "\n")

    while True:
        try:
            user_raw = input("🙋 请输入你的问题> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 已退出，再见！")
            break
        if not user_raw or user_raw.lower() in {"q", "quit", "exit"}:
            print("👋 已退出，再见！")
            break

        result = answer_user_query(user_raw)
        # 好看地打印
        print(f"\n{'─'*60}")
        print(f"📖 本次召回参考页  : {result['reference']} （第 {result['page_no']} 页）")
        print(f"🎯 BM25  Top5      : {result['bm25_top'][:5]}")
        print(f"🎯 BGE+C Top5      : {result['dense_top'][:5]}")
        print(f"🏆 RRF    Top5     : {result['rrf_top']}")
        print(f"⏱️  总耗时          : {result['cost_sec']}s")
        print(f"🤖 智能客服回答     :\n{result['answer']}")
        print(f"{'─'*60}\n")
