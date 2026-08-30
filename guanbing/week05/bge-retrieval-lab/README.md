# BGE 文本检索实验室 · 第一性原理手册

> **不依赖 ES / Milvus / FAISS**，只用 Python + NumPy，从零理解「Embedding → 相似度 → 排序」三步到底发生了什么。

---

## 0. 为什么会有这个项目？（第一性原理）

### 0.1 传统检索的本质问题
- **关键词匹配（TF-IDF / BM25 / ES 默认）** 只看「字面上有没有出现同一个词」。
- 例：`我今天很开心` vs `我今天心情很不错`
  - 关键词匹配：「开心」≠「心情很不错」→ **不相似** ❌
  - 但人一看就知道：**语义高度一致** ✅

> 所以我们需要一种能表示「语义」的数学对象 → **向量（Embedding）**。

### 0.2 本项目从第一性原理回答三个问题
| # | 问题 | 本项目里怎么落地 |
|---|------|------------------|
| Q1 | 一段中文文本，怎么变成能计算的向量？ | `SentenceTransformer.encode()` → 长度为 512 的浮点数组 |
| Q2 | 两段文本，怎么量化「它们有多像」？ | **余弦相似度**：向量夹角越小越相似，范围 [-1, 1]，1 = 完全相同 |
| Q3 | 从 N 条候选里找 Top-K？ | 对 query×docs 全算一遍相似度，`argsort(-scores)` 取前 K 条 |

> ✅ 这三件事就是 **所有** 向量检索系统的底层。ES / FAISS / Milvus 做的只是「把 Q2/Q3 用 C++ 加速 + 分桶 + 分片」，本质没变。

---

## 1. 项目结构

```
bge-retrieval-lab/
├── README.md                     ← 本文件（第一性原理手册）
├── FAQ.md                        ← 常见问题
├── requirements.txt              ← 依赖清单
├── BAAI/
│   └── bge-small-zh-v1.5/        ← 模型权重（执行 01_下载模型.py 后出现）
├── .cache/                       ← 所有缓存（避免写入 ~ 目录引发权限问题）
├── 01_下载模型.py                 ← Step 1
├── 02_最小检索Demo.py             ← Step 2（任务指定的 1 查询 + 3 文档）
└── 03_可复用向量库.py             ← Step 3（面向对象封装 SimpleBGEVectorStore）
```

---

## 2. 核心概念：每个词都值得展开

### 2.1 Embedding（嵌入 / 词向量 / 句向量）
- **定义**：把一段任意长度的文本，映射成固定维度 d 的浮点向量 `v ∈ ℝᵈ`。
- **为什么有用？**：训练时模型被迫学会「相似的句子 → 向量在空间中离得近」。
- **本项目选择的模型**：`BAAI/bge-small-zh-v1.5`
  - 中文领域权威（智源研究院 BAAI）
  - **small**：小版，参数量约 100M，Mac mini CPU/MPS 都能跑
  - **zh**：在中文语料上继续训练过，比纯英文 Sentence-BERT 效果好
  - **v1.5**：第二版，MTEB 中文榜单长期第一梯队
  - **d = 512**：输出固定 512 维，约 2KB/句

### 2.2 余弦相似度 Cosine Similarity
两条向量 `q` 和 `d`：

```
            q · d           dot(q, d)
sim(q, d) = ───── = ───────────────────────
          |q||d|     norm(q) * norm(d)
```

- `normalize_embeddings=True` 时 `|q|=|d|=1`，上式简化为 `sim = dot(q, d)`，**就是一次矩阵乘法**，速度极快。
- **取值范围** [-1, 1]
  - 1 → 完全相同方向
  - 0 → 完全无关 / 正交
  - -1 → 完全相反
- **中文文本检索的经验阈值**：
  - ≥ 0.75：非常强相关（同一句话的改写）
  - 0.55 ~ 0.75：强相关（你这个任务里的 0.6896 就落在这里）
  - 0.35 ~ 0.55：弱相关 / 有点关系
  - < 0.35：通常视为不相关

### 2.3 BGE 里非常关键的 Instruction 前缀
BGE 作者在训练时用了「指示微调」，因此**对 query 必须加一句 instruction**（doc 不要加）：

```python
INSTRUCTION = "为这个句子生成表示以用于检索相关文章："
q_vec = model.encode(INSTRUCTION + query, normalize_embeddings=True)  # ✅ 对
q_vec = model.encode(query, normalize_embeddings=True)                # ⚠️ 效果会差
```

本项目里的 [02_最小检索Demo.py](file:///Users/philipclaw/Downloads/padow-ai/Week5/bge-retrieval-lab/02_最小检索Demo.py) 和 [03_可复用向量库.py](file:///Users/philipclaw/Downloads/padow-ai/Week5/bge-retrieval-lab/03_可复用向量库.py) 都已正确实现。

### 2.4 为什么「不用 ES / FAISS / Milvus」也能学？
- 当候选文档数 ≤ 10 万条、向量维度 ≤ 1024 时，**朴素 NumPy 全量点积**在现代 CPU 上已经足够快（毫秒级）。
- 这让我们能**把 Q2/Q3 的每一步都看清**，而不是面对黑盒。
- 真正上生产时再换成 FAISS IVF-PQ / Milvus 即可，**数学定义完全一样**。

---

## 3. 快速开始（5 分钟跑起来）

```bash
# 1. 进入目录 + 激活环境
cd /Users/philipclaw/Downloads/padow-ai
source .venv/bin/activate
cd Week5/bge-retrieval-lab

# 2. 安装依赖（已装过可跳过）
pip install -r requirements.txt

# 3. 下载模型权重（首次约 190MB，自动走国内镜像）
python 01_下载模型.py

# 4. 运行任务指定的最小检索
python 02_最小检索Demo.py

# 5. 进阶：可复用向量库（3 组不同查询 × 6 条文档）
python 03_可复用向量库.py
```

---

## 4. 最小任务场景 · 预期输出对照

**查询**：`我今天很开心`

**文档库**：
- doc[0] 我喜欢机器学习
- doc[1] 我喜欢深度学习
- doc[2] 我今天心情很不错

**实测排名**：

| Rank | 相似度 | 文档 | 命中 |
|------|--------|------|------|
| 1 | 0.6896 | 我今天心情很不错 | ✅ 预期 Top 1 |
| 2 | 0.3750 | 我喜欢深度学习 | — |
| 3 | 0.3489 | 我喜欢机器学习 | — |

> 你看到 `0.6896 > 0.55` 就说明模型**识别出了「开心」和「心情很不错」是同一种语义**，而不是关键词重合。这正是 Embedding 相对 TF-IDF 的核心优势。

---

## 5. 计算到底发生了什么？（手算版）

以 02 Demo 为例，4 个步骤逐一展开：

| Step | 操作 | 维度变化 | 说明 |
|------|------|----------|------|
| ① Load | 加载 BGE 模型 | — | `dim = 512` |
| ② Encode Query | `instruction + "我今天很开心"` → `q_vec` | (512,) | 归一化后 `‖q‖ = 1` |
| ③ Encode Docs | `3 条中文` → `D ∈ ℝ^(3×512)` | (3, 512) | 每行归一化 `‖d_i‖ = 1` |
| ④ Compute & Sort | `scores = D @ q_vec` → `(3,)` | (3,) → (3,) | 点积就是余弦相似度，`argsort(-scores)` 得到排序 |

> **第 4 步是整个向量检索的核心公式**，只有一行 NumPy：
> ```python
> scores = (self.embeddings @ qv.reshape(-1, 1)).flatten()
> idx_sorted = np.argsort(-scores)
> ```
> 所有向量数据库做的事情，本质就是**加速这条公式**。

---

## 6. 进阶：`SimpleBGEVectorStore` API 设计

对应代码见 [03_可复用向量库.py](file:///Users/philipclaw/Downloads/padow-ai/Week5/bge-retrieval-lab/03_可复用向量库.py)：

```python
db = SimpleBGEVectorStore("./BAAI/bge-small-zh-v1.5")
db.add_documents(["doc1", "doc2", "doc3", ...])

hits = db.search(
    query="我今天很开心",
    top_k=3,
    threshold=0.4        # 相似度 < 0.4 的结果被丢弃
)
# hits = [(文本, 相似度), ...]，按相似度降序
```

这就是 FAISS IndexFlatL2 / Milvus Collection 的**极简教学版实现**，不到 50 行，包含
- 文档持久化存储（内存列表，可自行扩展到 `json.dump`）
- 向量矩阵 `vstack` 式动态扩容
- 阈值过滤 + Top-K 裁剪

---

## 7. 工程上值得注意的 5 件事

1. **所有缓存都重定向到项目 `.cache/`** —— macOS 沙盒经常不让写 `~/.modelscope` / `~/.cache/huggingface`，我们在脚本开头通过 `os.environ` 把 `MODELSCOPE_CACHE`、`HF_HOME`、`HUGGINGFACE_HUB_CACHE`、`SENTENCE_TRANSFORMERS_HOME` 全部设到本地，见 3 个脚本文件的头几行。
2. **normalize_embeddings=True 必须开** —— 否则 cosine 不是点积，容易写出 bug。
3. **query vs doc 编码规则不一致**：**只有 query 加 instruction**，doc 千万别加，否则两个向量分布不一致，相似度会乱。
4. **Apple Silicon 自动用 MPS**：`device = "mps" if torch.backends.mps.is_available() else "cpu"`，比纯 CPU 推理快 5~10 倍。
5. **小模型（small）的能力边界**：bge-small 擅长 50 字以内的短句匹配。如果是段落级（>200 字）文档，建议上 `bge-base-zh-v1.5`（dim=768）或 `bge-large-zh-v1.5`（dim=1024）。

---

## 8. 你现在掌握了什么？

```
关键词检索 (TF-IDF / BM25 / ES keyword)
    │
    │   只能字面匹配
    ▼
语义向量检索 = 本项目 02 Demo  +  03 SimpleBGEVectorStore
    │
    │   已经能解决 80% 的知识库 / FAQ / 文档召回
    ▼
工业级向量库（FAISS / Milvus / pgvector / Weaviate）
    │
    │   数学公式一模一样，差的是 千万级规模 / 分片 / 持久化 / 高并发
    ▼
混合检索 = 向量 + 关键词 + 重排序 (Reranker) ← Week 6 RAG 里你会见到
```

继续看 [FAQ.md](file:///Users/philipclaw/Downloads/padow-ai/Week5/bge-retrieval-lab/FAQ.md) 可以覆盖你 90% 的踩坑问题。
