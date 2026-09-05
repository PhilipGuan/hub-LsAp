# PageIndex 学习笔记：原理、实现与 RAG 工作流定位

> 配套脚本：
> - 教学简化版：[08_PageIndex.py](file:///Users/philipclaw/Downloads/padow-ai/Week7/Week07/08_PageIndex.py)（~237 行，核心逻辑无第三方 PageIndex 库依赖）
> - 生产调用版：[08_PageIndex2.py](file:///Users/philipclaw/Downloads/padow-ai/Week7/Week07/08_PageIndex2.py)（39 行，`pip install pageindex` 官方客户端）
> - 本地持久化产物：`.pageindex/docs/<doc_id>/{doc.json, pages.json, tree.json}`（GraphRAG 论文 26 页实测样例）

---

## 一、PageIndex 是什么？一句话定义

> **PageIndex =「基于"页码页块"为最小单元的 LLM 抽象分层索引 + LLM 路由选择器」**
> 通俗说法：先让 LLM 把 PDF **每 N 页** 读一遍，生成「标题 + 一句话摘要」的"目录卡片"；提问时先让 LLM 在这些目录卡片里挑 1-3 张"最相关的"，再把这些卡片对应的 PDF 原文灌给 LLM 做问答。

它是一种**介于"纯全文塞入"和"细粒度向量/倒排检索"之间的中粒度 RAG 范式**，核心设计哲学是：

- **不做精细检索**（不依赖 embedding、不做 BM25、不用分词）；
- **靠 LLM 的理解能力做两次跳跃**：第一次是 "Indexing 时理解每 5 页并写摘要"，第二次是 "Query 时理解问题并选择摘要节点"；
- **选择后不做二次精排**，直接取对应页码原文作为 Context。

对应到 [08_PageIndex.py](file:///Users/philipclaw/Downloads/padow-ai/Week7/Week07/08_PageIndex.py) 里的 5 个核心函数：
`read_pdf → build_index → search_index → get_context → answer`

---

## 二、PageIndex 要解决什么问题？（动机 vs 传统 RAG 的痛点）

| 传统 RAG（细粒度向量/BM25）痛点 | PageIndex 的对应解法 |
|---|---|
| **全局理解型问题必败**：问"这篇论文的核心贡献是什么？" "作者在评估章节用了什么指标？"——向量只会召回含有"核心贡献"字面的 1-2 个 chunk，但"评估指标"散落在 3.3 节、4.1 节、附录等多个位置，Top5 chunk 凑不齐 | 先按 5 页块 LLM 总结成目录卡片，问"评估指标"时 LLM 直接选中对应 Method(3-8p) + Analysis(8-9p) + Results(9-11p) 三张卡片，相关章节整块读 |
| **Chunk 切分过碎 → 上下文割裂**：一页 PDF 表格+公式横跨 3 个 chunk，向量检索只召回中间 1 个 chunk，公式缺上下文看不懂 | PageIndex 按 N 页切，天然保留完整章节、图表、公式上下文；5 页是论文标准章节约长度（GraphRAG 实测 Method 3-8p / Results 9-11p） |
| **Chunk 过碎导致的召回冗余 + 去重难**：同一段结论被 10 个 chunk 重复，RRF 融合后仍有 40% 重复信息 | 按 5 页切块最多就 N/5 ≈ 26/5 = 5~6 个节点，天然低冗余 |
| **工程复杂度高**：要装 sentence-transformers + 选模型 + 调 chunk_size+overlap + 选 RRF/Rerank + 建 FAISS/ES/HNSW 索引 | 只需 pdfplumber + OpenAI SDK（DeepSeek 兼容协议），无其他依赖 |
| **跨语言 PDF 分词/Embedding 质量差**：中英混排、公式、图表文字，BM25 切词 & embedding 语义都很差 | LLM 直接吃 OCR 文本 + 理解+总结+路由，避开分词和嵌入两大道具 |

---

## 三、[08_PageIndex.py](file:///Users/philipclaw/Downloads/padow-ai/Week7/Week07/08_PageIndex.py) 五段式核心代码逐段解读

### 3.1 阶段 0：LLM 包装器（L18-L30）——统一调用入口

```python
MODEL = "deepseek-v4-flash"

def llm(prompt):
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "You are a helpful document assistant."},
            {"role": "user", "content": prompt},
        ],
        stream=False,
        reasoning_effort="high",
        extra_body={"thinking": {"type": "disabled"}}
    )
    return response.choices[0].message.content.strip()
```

**教学要点**：
- 用 DeepSeek v4 Flash（性价比最高的推理模型）构建 PageIndex；
- `thinking: disabled` 明确关闭 R1-style CoT 输出，避免 JSON 里混进 `<think>` 标签让 `json.loads` 崩；
- 整个脚本**所有 LLM 交互都走这一个函数**——改模型/改 base_url/加重试，只改这一处（DRY 原则）。

### 3.2 阶段 1：PDF 读取（L34-L46）——保持页码对齐的数组

```python
def read_pdf(pdf_path):
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            pages.append({"page": i + 1, "text": text})
    return pages
```

**教学要点**：
- `page: i+1` 很关键——**永远和人类看到的 PDF 真实页码对齐**，否则问答时要标注"第 24 页"就会出现 off-by-one；
- 空页用 `or ""` 兜底（PDF 扫描页、图片页可能 `extract_text` 返回 None，不兜会崩）；
- 数组结构后续既要给 `build_index` 做分组，又要给 `get_context` 做原文提取，是共享的数据源。

### 3.3 阶段 2：构建 PageIndex（L50-L94）——这是"索引构建"的核心

```python
def build_index(pages, pages_per_node=5):
    index = []
    for i in range(0, len(pages), pages_per_node):
        group = pages[i:i + pages_per_node]
        start_page = group[0]["page"]
        end_page = group[-1]["page"]
        text = "\n".join(page["text"] for page in group)

        result = llm(f"""
下面是 PDF 第 {start_page}-{end_page} 页：
{text[:12000]}
请总结这部分内容。
只返回 JSON：
{{"title": "简短标题", "summary": "一句话总结"}}
""")
        try:
            info = json.loads(result)
        except:
            info = {"title": f"Page {start_page}-{end_page}", "summary": result}

        index.append({
            "id": len(index),
            "start_page": start_page,
            "end_page": end_page,
            "title": info["title"],
            "summary": info["summary"]
        })
    return index
```

**教学要点（设计 4 大决策）**：

| 决策点 | 本脚本选择 | 为什么这么做？ | 可调方向 |
|---|---|---|---|
| **① 分组粒度 `pages_per_node=5`** | 每 5 页一个节点 | 对 10-50 页学术论文（本项目 GraphRAG 论文是 26 页）刚好是 1-2 节一个章节粒度；太大（20 页）→ LLM 总结跑题；太小（1 页）→ 节点多，`search_index` 选不准 | 长报告/合同=10 页；短 PPT=2 页；**是 PageIndex 最重要的超参数**（CLI 暴露 `--pages-per-node` 参数）|
| **② LLM 输入截断 `text[:12000]`** | 前 12000 字符（约 4000 tokens）| 防止 5 页长表格/附录超过模型 context；DeepSeek v4 Flash context=128k，12k 保守但稳妥 | 可以改 `text[:48000]`（约 16k tokens，仍然便宜）|
| **③ 节点结构 5 字段** | `id / start_page / end_page / title / summary` | 精简到最少——`id` 是 `search_index` 选择返回的主键；`start/end_page` 是 `get_context` 过滤原文的范围；`title/summary` 是"目录卡片"供选择的依据 | 升级版 PageIndex 会加 `key_items[]`（见 08_PageIndex2 样例 tree.json 的 key_items 字段）、`embedding`、`tags` |
| **④ JSON 解析 fallback** | try/except 兜底用原始 result | 模型偶尔输出 "好的，以下是 JSON：```json {...}```" 包裹，`json.loads` 会炸；兜底保证 `index` 不为空，不中断构建 | 可以加 `re.search(r'\{.*\}', result, re.S)` 提取 JSON 子串更鲁棒 |

### 3.4 阶段 3：查询路由选择（L98-L128）——LLM 版"检索器"

```python
def search_index(question, index):
    index_text = "\n".join(f"""
ID: {node["id"]}
Pages: {node["start_page"]}-{node["end_page"]}
Title: {node["title"]}
Summary: {node["summary"]}
""" for node in index)

    result = llm(f"""
用户问题：
{question}
PageIndex：
{index_text}
请选择最相关的 1-3 个节点。
只返回 JSON 数组，例如：[1, 3]
""")
    try:
        return json.loads(result)
    except:
        return [0]
```

**教学要点**：
- 这是 PageIndex 最"反直觉"的地方——**没有任何向量相似度/BM25 计算，全部靠 LLM 读目录卡片做语义匹配**；
- 为什么这能 work？因为 N 页论文 → N/5 ≈ 5 个节点，索引目录文本才 ~800 字符，LLM 用最便宜的 flash 模型读一遍只要 1~2 分，理解比 embedding 余弦相似度强得多；
- 返回 `[0]` 兜底（选第一个节点），比返回空好——空的话 `get_context` 拿不到任何东西。

### 3.5 阶段 4：拉取原文 Context（L132-L147）——页号范围过滤

```python
def get_context(node_ids, index, pages):
    context = []
    for node_id in node_ids:
        node = index[node_id]
        for page in pages:
            if node["start_page"] <= page["page"] <= node["end_page"]:
                context.append(f"""
===== Page {page["page"]} =====
{page["text"]}
""")
    return "\n".join(context)
```

**教学要点**：
- 时间复杂度 O(节点数 × 总页数)，26 页 × 3 节点 = 78 次比较完全没问题；百万级文档要把 `pages` 改成 dict `{page_num: text}` 让第 138 行从 O(P) 变 O(1)；
- 每一页用 `===== Page X =====` 包裹头——**让最后 answer LLM 能按题面要求"标注相关页码"**（见 answer Prompt 要求 3）。

### 3.6 阶段 5：问答生成（L151-L175）——最终答案

```python
def answer(question, index, pages):
    node_ids = search_index(question, index)
    context = get_context(node_ids, index, pages)
    result = llm(f"""
根据下面 PDF 原文回答问题。
问题：
{question}
原文：
{context[:30000]}
要求：
1. 只能根据原文回答
2. 找不到答案就明确说明
3. 标注相关页码
""")
    return node_ids, result
```

**教学要点**：
- `context[:30000]` 二次截断：3 节点 × 5 页 = 15 页 × 每页 3000 字符 ≈ 4.5 万字符 → 截到 3 万字符（约 1 万 tokens）刚好塞 DeepSeek v4 Flash，且价格低；
- 三条要求是**防幻觉三件套**：原文约束（Faithfulness）+ 拒答（Abstain）+ 引用页码（Attribution）——和我们 Week6 RAG101 08/09 脚本的 Prompt 规范完全一致。
- 返回值多了一个 `node_ids`——让 main 循环能打印「检索节点 ID」，调试选得准不准非常直观（比如问 "community detection" 应该选 node 2=Method 页 4-8，如果选了 node 0 就是路由错了）。

### 3.7 CLI 入口（L179-L233）——离线一次性构建 + 交互式问答

```bash
# 用法（L178 注释给了实际例子）
python 08_PageIndex.py 资料/2404.16130v2-GraphRAG.pdf
```

**教学要点**：
- 构建只做一次（main L201-L215），然后进入 `while True` 死循环问答——**重要：离线构建的 index 没持久化，脚本退出就丢**（生产版 08_PageIndex2.py 的 `.pageindex/` 就是解决这个的，见下一节）；
- 实际教学优化方向：加 `--cache index.json` 参数，`build_index` 前先读缓存，省重复 LLM 调用的钱（见 §7 优化建议）。

---

## 四、升级版：官方 PageIndex 客户端 + 本地持久化结构（08_PageIndex2.py + .pageindex/）

[08_PageIndex2.py](file:///Users/philipclaw/Downloads/padow-ai/Week7/Week07/08_PageIndex2.py) 用 `pip install pageindex` 的官方客户端，和我们教学版的关系就像"SQLAlchemy ORM vs 手写 sqlite3 SQL"——核心思想完全一样，只是：
- 多了**树状分层结构**（不止 5 页平级，是 Section → Subsection 层级递归，Methods 节点下嵌套了 GraphRAG Workflow / Question Generation / Evaluation 三个子节点）
- 多了**文档元数据 doc.json**（doc_id、描述、页数、完成状态、createdAt）
- 多了**全量 OCR 结构化 pages.json**（每页 OCR 出来的 markdown 格式文本，比 pdfplumber 提取更规整，公式/表格保留 markdown 表格格式）
- 多了**持久化到 `.pageindex/`**，不用每次构建；每个文档只构建一次

### 4.1 实测 .pageindex 结构（GraphRAG 论文 26 页）

```
.pageindex/
├── manifest.json                              文档清单索引
└── docs/pi-bb831222ab834527829560b6d7a468bc/
    ├── doc.json         # 文档元信息：名称、描述、26页、status=completed
    ├── pages.json       # 26页数组，每页 OCR markdown（GraphRAG 论文实际 26 页）
    └── tree.json        # 树状 PageIndex（分层嵌套，不是教学版平级）
```

### 4.2 tree.json 的分层结构解读（摘录 VERBATIM，对应 [实际 tree.json](file:///Users/philipclaw/Downloads/padow-ai/Week7/Week07/.pageindex/docs/pi-bb831222ab834527829560b6d7a468bc/tree.json)）

```
Root level (6 个平级节点，对应 GraphRAG 论文章节):
├─ [0000] Introduction            页 1-2   key_items=["Abstract"]
├─ [0001] Background              页 2-4   key_items=[RAG KG RAG评价]
├─ [0002] Methods                 页 4-8   ← 父节点带 nodes 字段嵌套
│    ├─ [0003] GraphRAG Workflow                 页 4-6
│    ├─ [0004] Global Sensemaking Q Generation   页 6-7
│    └─ [0005] Evaluation Criteria               页 7-8
├─ [0006] Analysis                页 8-9
├─ [0007] Results                 页 9-11
└─ ...
```

**分层 PageIndex 的教学意义**：教学版把 5 页作为节点，Methods 3-8 页的 "Workflow/Question/Evaluation" 三个子主题揉在一个节点里，查询路由只能选择整节；升级版官方 PageIndex 会自动把一个 5 页的大节点再拆成 1-2 页的子节点——这样问"GraphRAG 怎么生成评测问题？"（只涉及 6-7 页），路由会选子节点 0004，而不是父节点 0002 的整 4-8 页，context 更精确、更少噪音。

---

## 五、PageIndex 在整个 RAG 谱系中的定位（和 Week5/6 的 TF-IDF/BM25/BGE/ES 对比）

| 维度 | PageIndex（本脚本）| 稀疏检索（TF-IDF / BM25 / ES 倒排 Week6）| 稠密向量检索（BGE/FAISS Week5）| GraphRAG（Week7 后续内容）|
|---|---|---|---|---|
| **检索原理** | LLM 读摘要卡片 → 选 1-3 个节点 | Term 倒排 + BM25 打分（词面 TF×IDF）| Embedding 余弦相似度（HNSW 近似近邻）| 抽实体 → Leiden 社区检测 → 社区摘要 → Map-Reduce 多社区汇总 |
| **最小单元** | N 页（默认 5 页）= 几千字 ~ 1 章节 | Chunk（40-512 字符，几句话）| Chunk（128-512 字符）| 实体级（比 Chunk 还细）→ 社区级（聚类后摘要）|
| **需要 Embedding 模型？** | ❌ 完全不需要 | ❌（BM25 需要 jieba/IK 分词）| ✅ BGE/Jina 等 + MPS/GPU | ⚠️ 可选（实体消歧用）但主要靠 LLM 抽 |
| **需要倒排索引/HNSW？** | ❌ | ✅ ES 倒排 / rank_bm25 | ✅ FAISS / ES dense_vector | ❌（用图社区 + LLM，向量可选）|
| **工程复杂度** | 极低（pdfplumber + OpenAI SDK）| 中（jieba/IK + 索引持久化）| 高（模型下载 + GPU/MPS + HNSW 参数）| 极高（Week7_12_GraphRAG 全套流程）|
| **LLM 调用次数 / 百页论文** | 构建 20 次 + 每次问答 2 次（选节点+答）| 构建 0 次 + 每次问答 1 次（答案生成）| 构建 0 次 embedding + 每次问答 1 次 | 构建百次+（实体、关系、社区摘要…） + 每次问答 N 次 |
| **适用问题类型** | ✅ 章节级、跨节、全局理解型（论文主贡献/评估指标/工作流介绍）| ✅ 精确事实定位（"第 2.3 节 Adaptive benchmarking 引用了哪两篇？"）| ✅ 语义相似问答（"作者如何对比 vector RAG 和 graph RAG？"）| ✅ 最强全局理解（"全文涉及 RAG 评测的 5 种方法横向对比并举例"）|
| **典型失败场景** | ❌ 事实查找（"Algorithm 1 第 4 步是什么？"）——只选 5 页块的路由可能漏了具体算法块的 1 页 | ❌ 全局理解型（和向量 RAG 一样缺整块上下文）| ❌ 同义改写差、跨 chunk 上下文割裂 | ❌ 太贵、太慢（不适合教学快速原型）|
| **命中粒度检查方式** | 返回的 page range 是否覆盖题面要求章节 | 具体命中 chunk 页码/内容匹配 GT | 余弦相似度阈值 + GT 页号 Hit@N | LLM-as-judge 四维度打分（Comprehensiveness/Diversity/Empowerment/Directness）|
| **教学定位** | Week7 "LLM 本身当 Indexer + Retriever" 的入门哲学课 | Week5/6 稀疏检索基础 | Week5/6 稠密检索基础 | Week7 进阶、GraphRAG 论文复现 |

---

## 六、PageIndex 的核心思想：LLM 同时做 Indexer + Router + Generator（三职一体）

传统 RAG 三段式：
```
Source → [Embedding Model: Indexer] → VectorStore
Query  → [Similarity Search: Router]  → TopK Context → [LLM: Generator] → Answer
```

PageIndex 三段式：
```
Source → [LLM: Indexer] → PageIndex Cards（摘要+页码）
Query  → [LLM: Router]    → 选 1-3 张卡片 → 取原文 → [LLM: Generator] → Answer
```

**关键认知**：PageIndex 把「怎么切数据、怎么选相关内容」这两个最让 RAG 初学者头疼的工程问题，全丢给了 LLM 的自然语言理解能力解决——**用工程极简 + 少量 LLM 成本，换一个在 10-100 页 PDF 场景下几乎不会"完全答非所问"的基线**。

---

## 七、实用优化建议（教学版 → 可用版本的 5 个最小改动）

1. **加 index.json 缓存，省 80% 构建费**
   ```python
   # build_index 前加：
   CACHE = f".pageindex_cache_{os.path.basename(args.pdf)}.json"
   if os.path.exists(CACHE):
       with open(CACHE) as f: index = json.load(f)
   else:
       index = build_index(pages, args.pages_per_node)
       with open(CACHE, "w") as f: json.dump(index, f, ensure_ascii=False, indent=2)
   ```
   对应 08_PageIndex2.py 的 `.pageindex/` 持久化逻辑，教学版一行代码就补上。

2. **JSON 提取鲁棒化（防 ```json 代码块包壳）**
   ```python
   import re
   m = re.search(r'\{(?:[^{}]|\{[^{}]*\})*\}', result, re.S)
   info = json.loads(m.group()) if m else fallback
   ```

3. **search_index 加向量兜底双通道**：LLM 选节点失败概率虽低但存在（返回 [0]）。可以给每个 summary 算一个 512d BGE 嵌入，失败时走余弦 Top3 兜底。

4. **支持 `pages.json` 缓存 pdfplumber 结果**：26 页 PDF 提取只要 0.3s，但合同类 500+ 页 PDF 要几分钟，加缓存很值。

5. **Answer Prompt 增加 "如果找不到答案，输出检索节点 ID 并说明原因"**：对 LLM 路由选择错误的调试极有帮助——比如检索 node=[0,1]（Introduction/Background）但答案在 Methods 页 4-8，可以一眼看出是 `search_index` 选错了节点，再去调 Prompt 或 `pages_per_node`。

---

## 八、本脚本报错的 4 个常见坑（教学实测经验）

| # | 现象 | 根因 | 修复 |
|---|---|---|---|
| **P1** | `json.JSONDecodeError: Expecting value: line 1 column 1 (char 0)` | build_index / search_index 的 llm 返回不是 JSON（比如 "好的，这里是总结"），或前面有 `<think>` 标签没关掉 | ① 加 §7-2 re 提取 JSON 子串；② 如用 R1 模型，必须保留 `extra_body={"thinking": {"type": "disabled"}}`（脚本已经加了，别删）|
| **P2** | pdfplumber 提取中文公式全是乱码 → summary 质量差 | 扫描版 PDF / 图片 PDF pdfplumber OCR 不了 | 换 `pymupdf`（fitz）或给官方版 PageIndex 做（它内部有更强 OCR，pages.json 里输出的 markdown 质量明显好）|
| **P3** | 问细节问题回答"原文未提及"但其实原文有 | 节点选粗了：5 页路由选了 Methods 4-8，但具体算法在第 6 页的子节点（教学版平级没拆分） | ① `--pages-per-node 2` 改细；② 用官方版 PageIndex 2（有层级 tree.json）；③ 或结合 Week6 ES 向量/BM25 做 Hybrid |
| **P4** | `AttributeError: 'NoneType' object is not subscriptable`（index [node_id] 报错）| search_index 返回 `[0]` 但实际只有 0 个节点？不，是 `node_ids` 含有超出 `len(index)` 的数字（模型幻觉选了个 99） | `node_ids = [n for n in node_ids if isinstance(n,int) and 0 <= n < len(index)] or [0]` |

---

## 九、总结：PageIndex 适合/不适合什么场景？

### ✅ 最适合的 4 种场景
1. **10-100 页学术论文、技术方案、合同审查**：刚好分 2-20 个节点，LLM 路由几乎不会错；
2. **教学 Demo / 快速原型**：5 分钟写完脚本，30 秒构建完 26 页论文索引；
3. **全局理解型问题为主**（"本文 3 大核心贡献""评估指标有哪些""工作流分几步"）；
4. **预算有限、不想调 embedding/分块/融合超参**：用 flash 模型跑一次 26 页才几分钱，远低于调一周超参的人力成本。

### ❌ 不适合的 3 种场景
1. **1000+ 页大文档**：N/5 = 200+ 节点，`index_text` 塞 `search_index` LLM 读不完 + 读一次贵 + 选不准；
2. **精确事实定位**（"第 5 行代码是什么""表格 2 第 3 列第 7 个数字"）：5 页块精度天然不够，不如 Week6 ES + BM25 + 向量 Hybrid；
3. **100ms 低延迟检索**：`search_index` 要打一次 LLM（~500ms-2s），纯向量检索只要 5-20ms，不在一个量级。

### 🎯 在 Week7 RAG 进阶课里的教育意义
- Week5-6 教的是「RAG 的经典范式」——切 chunk → embedding/倒排 → 相似召回 → 精排 → 生成；**工程复杂、超参多、但工业化成熟**。
- Week7 开篇 PageIndex 教的是「RAG 的另一个极端」——**LLM 本身就是 indexer + retriever + generator，工程极简、几乎零超参，靠 LLM 理解能力把 RAG 所有难题一笔带过**。
- 当你真正在 Week7 后面学到 GraphRAG（实体/社区/摘要/Map-Reduce），你会发现它其实是**把 PageIndex 的"页码页块"换成了"图社区摘要"**——核心思路一模一样（分层摘要 + LLM 路由 + 原文拉取），只是分块的依据从"页码连续"变成了"图结构强关联"。理解 PageIndex 就是理解 GraphRAG 的一半。
