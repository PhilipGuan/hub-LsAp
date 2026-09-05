# RAG101_09：汽车智能客服 · CLI 交互式任意问题问答（RAG 两阶段：BM25+BGE 多粒度 + RRF + Cross-Encoder 精排）

> 对应脚本：[RAG101_09_交互式问答.py](RAG101_09_交互式问答.py)
> 适用教材：Week6 第 03/05/08/09 讲（BM25 → BGE → RRF 融合 → Cross-Encoder 重排 → LLM 回答的完整工作流）
> 验证平台：macOS Apple Silicon (MPS 后端) / Linux CUDA / 纯 CPU 均可跑；Python 3.10+

---

## 一、这个脚本能做什么？

对比同系列 `RAG101_08_RAG问答.py`（**批处理作业模式**：固定 `questions.json` 301 题批量跑，严格复刻 labels.json 判分口径），本 09 脚本是 **CLI 智能客服模式**：

| 能力 | RAG101_08（批处理作业） | RAG101_09（本脚本 · 交互式） |
|---|---|---|
| 问题来源 | 固定 questions.json 301 题 | ✅ **用户任意输入**，回车即答 |
| Chunker | 单粒度 40char 0 overlap | ✅ **多粒度 [(40,0), (128,20), (256,48)] merge 去重**（修复 2） |
| 召回精排 | BM25 + BGE → RRF 粗排（Hit@1≈67%） | ✅ RRF 粗排 Top3 后 **Cross-Encoder (bge-reranker-base) 精排**（修复 1，Hit@1≈+3~4pp） |
| Prompt 模式 | 作业严格模式（含"如果无法回答请拒答"） | ✅ **双模式开关**：默认客服宽松模式（并列清单主动整理为步骤 1/2/3…）；可切 True 复刻 08 口径 |
| 拒答后处理 | `if "无法" in answer` 朴素子串（会误伤原文"切勿强行卡入"） | ✅ **保守拒答**：前 60 字出现"无法"或完整拒答短语命中才替换 |

### 典型单题表现（来自 labels.json 原题，端到端跑通）

| 题目 | 召回 Top1 | GT 关键要点命中 |
|---|---|---|
| 储物空间：如何合理安排前排储物空间以提高实用性？ | page_24 ✅ | 16/16（100%） |
| 防盗系统：如何确保车辆的防盗系统正常工作？ | page_48 ✅ | 8/8（100%） |

---

## 二、目录结构 & 你最少需要哪些文件才能跑通

> 如果你只把 `RAG101_09_交互式问答.py` 这 1 个文件单独传到 GitHub，**别人 clone 下来 100% 跑不通**。最少需要下面这个集合：

```
Week06/                          ← 你应该整个上传 Week06 文件夹（或至少以下文件）
├── RAG101_09_交互式问答.py       ← 主脚本（必须）
├── README.md                     ← 本说明文件（必须，否则别人看不懂怎么配置）
├── requirements.txt              ← pip install -r 用（必须，已为本脚本生成）
├── .env.example                  ← 大模型 Key 配置模板（必须；真实 .env 请 gitignore 不要传）
├── .env                          ← ⚠️  填了真实 Key 的 .env 千万不要传到 GitHub！
├── 汽车知识手册.pdf              ← 召回目标文档（必须，脚本 L104 会 pdfplumber.open 读它）
│
├── models/                       ← 在 Week06 的**上一级目录** Week6/models/（见下方 §三 准备）
│   └── BAAI/
│       ├── bge-small-zh-v1.5/    ← BGE Bi-Encoder 稠密向量模型（脚本 L175 读 ../models/...）
│       └── bge-reranker-base/    ← Cross-Encoder 精排模型（脚本 L199 读 ../models/...，可选；不装会自动降级只跑 RRF）
│
├── labels.json                   ← 可选：如果你想对照 GT 做评估（不影响交互式问答功能）
└── questions.json                ← 可选：同上（不影响 09 功能）
```

---

## 三、环境准备（3 步搞定）

### Step 1 · 安装 Python 依赖

```bash
# 1. （强烈推荐）建个虚拟环境
python3 -m venv .venv
source .venv/bin/activate         # macOS / Linux
# .venv\Scripts\activate          # Windows

# 2. 装包
pip install -r requirements.txt
```

### Step 2 · 申请大模型 API Key 并写进 `.env`

本脚本支持两家云端大模型，任选一家（默认 DeepSeek，性价比更高）：

- **DeepSeek**（推荐）：去 [https://platform.deepseek.com/](https://platform.deepseek.com/) 申请 Key，模型推荐默认 `deepseek-v4-flash`。
- **Qwen（通义千问 / DashScope）**：备选，去 [https://dashscope.console.aliyun.com/](https://dashscope.console.aliyun.com/) 申请 Key。

填配置：

```bash
cp .env.example .env
# 然后打开 .env，把 sk-xxx 占位符替换成你自己的真实 Key
```

### Step 3 · 下载两个本地向量/重排模型（BGE + bge-reranker-base）

脚本里写死了模型路径在 Week06 **上一级**的 `Week6/models/BAAI/`：

```
Week6/
├── Week06/         ← 本 README 在这里
└── models/
    └── BAAI/
        ├── bge-small-zh-v1.5/       ← Bi-Encoder（必装）
        └── bge-reranker-base/       ← Cross-Encoder 精排（推荐，可选降级不装）
```

**推荐方式 A（modelscope，国内快，已经验证成功）**：

```bash
# 0. 先把 modelscope 缓存放在项目内，避免 ~/.modelscope 沙箱权限问题
export MODELSCOPE_CACHE=/path/to/Week6/.modelscope_cache
export HOME=$MODELSCOPE_CACHE

# 1. BGE 小模型（稠密召回用）
modelscope download --model BAAI/bge-small-zh-v1.5 \
  --local_dir /path/to/Week6/models/BAAI/bge-small-zh-v1.5/

# 2. bge-reranker-base（Cross-Encoder 精排用，不装也能跑，只是 Hit@1 会回退约 3pp）
modelscope download --model BAAI/bge-reranker-base \
  --local_dir /path/to/Week6/models/BAAI/bge-reranker-base/
```

**方式 B（HuggingFace，海外网络快时用）**：

```bash
cd /path/to/Week6/models/BAAI/
git lfs install
git clone https://huggingface.co/BAAI/bge-small-zh-v1.5
git clone https://huggingface.co/BAAI/bge-reranker-base
```

如果你忘了装 bge-reranker-base，脚本不会崩——会打印一行降级提示，然后跳过 Cross-Encoder 精排阶段，**只保留 BM25 + BGE 多粒度 + RRF** 的粗排（baseline Hit@1≈67%）。

---

## 四、运行方式

### 4.1 交互式问答（最常用）

```bash
python3 RAG101_09_交互式问答.py
```

启动后 CLI 会这样：

```
============================================================
🚗 汽车智能客服 RAG Demo 已就绪（基于 Week6 03+05+08）
   · 输入任意汽车相关问题，回车即可获得回答（含召回页 + RRF 详情）
   · 输入 q / quit / exit / 直接回车 → 退出程序
============================================================

🙋 请输入你的问题> 如何合理安排前排的储物空间以提高实用性？
📖 召回参考页：page_24 第 24 页

🤖 回答：
根据资料，前排储物空间可参考以下方式安排以提高实用性：
1. 遮阳板上的卡片/票据夹：存放卡片、票据等轻薄物品。
2. 无线充电板/置物板：放置手机或作为临时置物区域。
……（略）

🙋 请输入你的问题> q
👋 已退出，再见！
```

### 4.2 作为库 import（不想走 CLI，想用 Python 代码调）

```python
import importlib.util, sys, os
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "rag09", os.path.join(SCRIPT_DIR, "RAG101_09_交互式问答.py"))
M = importlib.util.module_from_spec(spec); sys.modules["rag09"] = M
spec.loader.exec_module(M)                              # 这里会用 10~15s 建索引

r = M.answer_user_query("如何确保车辆的防盗系统正常工作？")
print(r["reference"])   # → 'page_48'
print(r["answer"])      # → 分点回答
print(r["rrf_top"])     # → RRF Top5 (page, score) 列表，诊断召回用
```

---

## 五、两个核心修复（修复 1 + 修复 2）实现说明

> 详细的问题诊断过程与 A/B 试验数据，见同目录下的 [RAG101_问题诊断与修复方案记录.md](RAG101_问题诊断与修复方案记录.md)。下面只给代码级结论。

### 修复 2 · 多粒度 Chunker（为什么原来单粒度不够？）

**问题**：baseline 单粒度 `40char 0 overlap` 把 page_24 "前排储物空间总览"的 6 项并列清单切得太碎，每个 40char 片段只含 2~3 个部件名 → BGE cosine 算出来的语义相似度永远干不过整段语义完整的干扰页。

**代码位置**：[RAG101_09_交互式问答.py:L125-L162](RAG101_09_交互式问答.py#L125-L162)

核心片段：

```python
CHUNK_GRANS = [(40, 0), (128, 20), (256, 48)]
for page in pdf_pages:
    seen_this_page = set()
    for size, overlap in CHUNK_GRANS:
        for chunk_text in split_text_with_overlap(page["content"], size, overlap):
            if chunk_text not in seen_this_page:
                seen_this_page.add(chunk_text)
                pdf_chunks.append({"page": page["page"], "content": chunk_text})
```

**三种粒度各司其职（并行互补，不打架）**：
- 40char：抓字面关键词（"杯托""眼镜盒"等细粒度词对）；
- 128char + 20 overlap：覆盖 3~4 个并列储物部件的组合语义；
- 256char + 48 overlap：覆盖**完整的 "前排储物空间" 总览 + 注意事项**，这是 baseline 完全缺失的粒度，也是让 GT page_24 BGE 排名从 #4 进 #3 的关键。

### 修复 1 · RRF Top3 之后 Cross-Encoder 精排（为什么 RRF 之后还要再排一次？）

**问题**：储物空间题 RRF 粗排 Top3 是 page_26(0.03279) > page_3(0.03254) > page_24(0.03227)，page_24 只差 0.0005 落选 Top1。这个量级**继续调 RRF k 值、调两路权重都是瞎调**（改变不了 BM25 把干扰页放 #1 的事实）。必须换一个更准的打分器在小范围 TopN 上精排。

**代码位置**：
- 离线加载模型：[RAG101_09_交互式问答.py:L183-L198](RAG101_09_交互式问答.py#L183-L198)
- 在线精排：[RAG101_09_交互式问答.py:L228-L253](RAG101_09_交互式问答.py#L228-L253)

核心片段：

```python
# ---------- 离线初始化（只跑 1 次，~3s 加载 CrossEncoder 201 个权重块）----------
RERANK_TOPK = 3                    # RRF Top3 拿来精排
RERANK_MAX_TEXT = 700              # 中文约 700 字 = 512 WordPiece（bge-reranker-base 硬上限）
reranker = CrossEncoder(RERANK_MODEL_PATH, device=_device)

# ---------- 在线每次 query 都跑（3 对 pairs 约 0.8s）----------
K = min(RERANK_TOPK, len(sorted_pages))
topK_pages = sorted_pages[:K]
pairs = [(query, pdf_content_dict.get(p,"")[:RERANK_MAX_TEXT]) for p,_s in topK_pages]
ce_scores = reranker.predict(pairs, convert_to_numpy=True, show_progress_bar=False)
zipped = sorted(zip(ce_scores, topK_pages), key=lambda x: (x[0], x[1][1]), reverse=True)
sorted_pages[:K] = [page for _, page in zipped]
```

**为什么 700 字截断？**：Cross-Encoder 把 `<[BOS_never_used_51bce0c785ca2f68081bfa7d91973934]> Q [SEP] D [SEP]` 拼起来一起进 BERT，WordPiece 硬上限是 512。中文 1 字≈0.7 WordPiece，文档喂 700 字 + query 约 30 字 ≈ 520 WordPiece，刚好不触发 tokenizer 静默 truncate（否则 page_24 后半段的"中央扶手储物箱/眼镜盒/切勿强行卡入杯托"会被偷偷丢掉，精排就白做了）。

### 修复 1 与 修复 2 为什么**不冲突**？（DAG 正交性证明）

```
[修复 2：多粒度 Chunker] → pdf_chunks → BGE encode → pdf_chunk_embeddings
                                                         ↓
用户 query → BM25（按页）→ bm25_top_pages        两路互不相交
        ↘  BGE（按 chunk）→ dense_top_pages
                              ↓
                         RRF 粗排（k=60）
                              ↓
                     [修复 1：Cross-Encoder]（只读 RRF Top3 + 全局 pdf_content_dict，不回写上游）
                              ↓
                         sorted_pages (Top1 给 LLM)
```

- 修复 2 只**写**离线索引变量，不影响在线流程；
- 修复 1 只**读** RRF 输出 + 全局只读字典，不回写上游；
- 没有共享状态、没有数据回环，是严格 **DAG 上游 → 下游** 关系，1+1>2：修复 2 保 GT 一定进 RRF Top3（给修复 1 喂数据），修复 1 保 GT 一定在 Top3 里翻牌拿第一。

---

## 六、双模式开关：`SWITCH_HOMEWORK_MODE`（09 脚本如何做到"一套代码，两种用法"？）

在脚本 L54 有一个全局开关：

```python
SWITCH_HOMEWORK_MODE = False   # ← 改这里
# True  = 作业严格模式：100% 复刻 RAG101_08 的 Prompt + 拒答逻辑（交作业时判分口径一致）
# False = 智能客服宽松模式（默认，用于用户自定义问答）
```

两种模式影响两个地方：

| 环节 | 作业模式（True） | 客服模式（False，默认） |
|---|---|---|
| **Prompt 模板** [L270-L304](RAG101_09_交互式问答.py#L270-L304) | 完全复刻 08：含两句"如果无法回答请回答无法回答" | 宽松规则 4 条：<br>1. **并列部件清单本身就是安排方法的一部分，整理为 1/2/3…回答，不要因为没出现"如何/怎么"就拒答** ← 这句话解决了储物空间题 page_24 是纯清单、LLM 开口拒答的问题<br>2. 资料真没答案才拒答<br>3. 不要编造<br>4. 结尾总结注意事项 |
| **拒答标准化** [L330-L345](RAG101_09_交互式问答.py#L330-L345) | `if "无法" in answer:` → 直接替换（朴素子串，100% 复刻 08，保证判分统一） | **保守三选一**（避免引用原文"切勿强行卡入/无法开启防盗系统"被误伤）：<br>(A) 前 60 字出现"无法"（LLM 一开口就拒答的典型模式）<br>OR (B) 完整拒答短语命中（"无法回答/无法从资料/无法确定…"共 8 条）<br>OR (C) 回答为空白（兜底） |

---

## 七、常见问题 FAQ

### Q1：我只传了 `RAG101_09_交互式问答.py` 一个文件，别人能跑吗？
**答：100% 跑不通**。至少需要：
- 同目录下的 `汽车知识手册.pdf`（否则 `pdfplumber.open` 直接 FileNotFound）；
- 同目录下的 `.env`（否则 `DEEPSEEK_API_KEY` 是空字符串，63 行直接 RuntimeError）；
- `../models/BAAI/bge-small-zh-v1.5/` 模型（否则 175 行 SentenceTransformer 会尝试联网自动拉，国内网络大概率卡死或失败）；
- `requirements.txt` 依赖清单（否则别人不知道要装 `sentence-transformers`、`rank_bm25`、`pdfplumber` 这些第三方包）。

→ 最简单、最不会漏的方式：**整个上传 `Week6/Week06/` 文件夹（配合 `Week6/models/` 的 README 指引手动下模型）**，见上方 §二 目录结构。

### Q2：启动报错 `RuntimeError(".env 中 DeepSeek 配置不完整...")`
答：看本 README §三 Step 2，把 `.env.example` 复制成 `.env`，填上你自己的真实 API Key。注意 `.env` 文件**一定在 Week06 目录下**（脚本 `os.path.join(SCRIPT_DIR, '.env')` 读的就是这个位置）。

### Q3：启动 `SentenceTransformer(...)` 卡了 1 分钟还没动静
答：99% 是本地 `../models/BAAI/bge-small-zh-v1.5/` 不存在，sentence-transformers 尝试从 HuggingFace 联网下载，国内网络拉不动。按 §三 Step 3 **用 modelscope 手动把两个模型下到本地正确路径**即可。

### Q4：Cross-Encoder 精排不工作，日志说"降级不启用"
答：说明 `../models/BAAI/bge-reranker-base/` 不存在或路径不对。脚本不会崩，会正常跑但少了 3pp Hit@1 左右的增益。如果在意准确率就把模型放到正确路径。

### Q5：储物空间题召回对了（page_24），但 LLM 还是"无法回答问题"
答：99% 是把 `SWITCH_HOMEWORK_MODE` 切到 `True` 了（作业严格模式）。作业模式的 System Prompt 里两次说"如果无法回答请拒答"，而 page_24 是纯并列清单，没出现"步骤/如何/安排"等动词，LLM 会按严格模式拒答。切回 `False`（默认客服宽松模式）即可，宽松模式 Prompt 规则 1 明确写了"并列部件清单本身就是安排方法的一部分，整理为分点步骤回答"。

---

## 八、配套文件索引

| 文件名 | 作用 | 必传 GitHub？ |
|---|---|---|
| [RAG101_09_交互式问答.py](RAG101_09_交互式问答.py) | 本主脚本 | ✅ 必传 |
| [RAG101_问题诊断与修复方案记录.md](RAG101_问题诊断与修复方案记录.md) | 储物空间题从基线 0/16 → 16/16 的完整排查 A/B 试验记录 | ⚠️ 强烈推荐传（教学价值高），但不影响功能 |
| [RAG101_08_RAG问答.py](RAG101_08_RAG问答.py) | 批处理作业模式，参考对照 | 可选（09 功能不依赖它） |
| `汽车知识手册.pdf` | 召回目标文档 | ✅ 必传（否则脚本一运行就 FileNotFound） |
| [requirements.txt](requirements.txt) | pip 依赖清单 | ✅ 必传 |
| [.env.example](.env.example) | 大模型 Key 配置模板 | ✅ 必传（真实 `.env` ⚠️ 千万别传） |
| `labels.json` / `questions.json` | 301 题题库 + GT 答案，评估用 | 可选（09 功能不依赖） |

---

*本 README 为 Week6 第 09 讲交互式问答配套，若发现笔误或依赖有更新，请直接提 PR 修改本文件，谢谢！*
