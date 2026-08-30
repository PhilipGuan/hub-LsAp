# BGE 文本检索实验室 · 常见问题 (FAQ)

> 所有问题均为 macOS + Apple Silicon + 虚拟环境场景下真实踩坑记录。

---

## 一、安装 / 依赖相关

### Q1. `pip install sentence-transformers` 特别慢 / 卡住？
**原因**：默认走 PyPI 官方源。  
**解决**：临时切清华源
```bash
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple sentence-transformers modelscope
```

### Q2. 提示 `ModuleNotFoundError: No module named 'sentence_transformers'`？
**原因 1**：没激活项目虚拟环境。一定要先：
```bash
cd /Users/philipclaw/Downloads/padow-ai
source .venv/bin/activate
which python   # 应输出 .../padow-ai/.venv/bin/python
```
**原因 2**：在 IDE 里点 ▶ 运行，但 IDE 的 Python Interpreter 没选到 `.venv`。
解决：VS Code / PyCharm 的 Select Interpreter 里挑 `.venv/bin/python`。

### Q3. `sentence-transformers` 和 `sentence_transformers` 有什么区别？
- 安装时用横杠：`pip install sentence-transformers`
- 代码里 `import` 用下划线：`from sentence_transformers import SentenceTransformer`
- 这是 Python 包命名的常规约定（PyPI 名 vs 模块名），两者是同一个东西。

---

## 二、下载模型相关（出现率最高的一类问题）

### Q4. `modelscope` 报错：`Operation not permitted: '/Users/philipclaw/.modelscope'`
**原因**：`modelscope_hub` 内部在 import 时就尝试 mkdir `~/.modelscope`，如果你的 macOS 沙盒 / IDE 权限受限，就会被拦截。  
**解决**：本项目已经在所有 3 个脚本的**最开头**（甚至 import sentence_transformers 之前）重定向了所有缓存目录：
```python
os.environ["MODELSCOPE_CACHE"] = "./.cache/modelscope"
os.environ["HF_HOME"]              = "./.cache/huggingface"
os.environ["HUGGINGFACE_HUB_CACHE"]= "./.cache/hf_hub"
```
所以你**只要直接 `python 01_下载模型.py` 就行**，不要自己额外在 `~/.bashrc` 里配。

如果仍失败，直接用脚本末尾提示的命令，在一个**全新的、不走沙盒的 Terminal.app 里**手动执行：
```bash
cd /Users/philipclaw/Downloads/padow-ai/Week5/bge-retrieval-lab
export MODELSCOPE_CACHE=$PWD/.cache/modelscope
modelscope download --model BAAI/bge-small-zh-v1.5 --local_dir $PWD/BAAI/bge-small-zh-v1.5
```

### Q5. 用 `modelscope download` 命令时报 `zsh: command not found: modelscope`？
**原因**：没进 `.venv`，或者 `PATH` 里没带上。  
**解决**：用 `python -m` 方式调用，百分百能找到：
```bash
python -m modelscope download --model BAAI/bge-small-zh-v1.5 --local_dir ./BAAI/bge-small-zh-v1.5
```

### Q6. 下载了一半 Ctrl+C，下次会不会重下？
- **HF Hub / huggingface_hub**：断点续传是默认开启的，安全。
- **modelscope**：新版本支持 `cache_dir` 缓存；如果想 100% 确保断点，先别手动 `rm -rf .cache/`。

### Q7. HuggingFace 连接超时 `ReadTimeout`？
脚本里已经会自动尝试 `https://hf-mirror.com` 镜像端点。如果你是在 VPN 环境下也可以手动设：
```bash
export HF_ENDPOINT=https://hf-mirror.com
python 01_下载模型.py
```

---

## 三、运行 / 代码相关

### Q8. `RuntimeError: MPS backend out of memory`？
**原因**：一次 encode 太多文档爆了 Apple Silicon 共享显存。  
**解决**：`encode()` 加 `batch_size=8`（默认是 32）：
```python
doc_vecs = model.encode(DOCUMENTS, normalize_embeddings=True, convert_to_numpy=True, batch_size=8)
```

### Q9. 相似度全部非常接近，比如都是 0.98，区分不开？
**排查顺序**：
1. 有没有对 query 加 instruction？ → 必须加（见 README 2.3）。
2. `normalize_embeddings=True` 开了吗？ → 必须开。
3. 是不是 query 和 doc 都加了 instruction？ → **只给 query 加**，两边都加会让相似度整体偏高。
4. 是不是重复的文档？ → 检查 DOCUMENTS 列表。

### Q10. 相似度是负数？正常吗？
**正常**。余弦相似度理论范围 [-1, 1]。
- 出现负数说明两条文本的语义「正交 / 甚至相反」，比如「我今天很开心」 vs 「我今天特别难过」（情绪相反）。
- 真实世界里，因为 BGE 向量空间分布比较集中，**常见区间是 [0, 0.85]**，负数很少，但不是 bug。

### Q11. `FutureWarning: The get_sentence_embedding_dimension method has been renamed...`
**原因**：`sentence-transformers 6.x` 把方法改名了（`get_sentence_embedding_dimension` → `get_embedding_dimension`）。
**解决**：本项目 02/03 脚本里已经加了兼容写法，不会再出警告。如果是你自己写的代码，替换成：
```python
dim_fn = getattr(model, "get_embedding_dimension", None) or model.get_sentence_embedding_dimension
dim = dim_fn() if callable(dim_fn) else dim_fn
```

### Q12. `Unrecognized model in BAAI/bge-small-zh-v1.5. Should have a model_type key in its config.json`
**原因**：`SentenceTransformer(MODEL_NAME)` 直接从 HF Hub 拉，但模型配置文件在 HuggingFace 上的元数据偶尔对不齐。
**解决**：先通过 `01_下载模型.py` 把完整权重落盘，然后**传入本地目录路径**而不是 HF ID：
```python
model = SentenceTransformer("./BAAI/bge-small-zh-v1.5")   # ✅
model = SentenceTransformer("BAAI/bge-small-zh-v1.5")     # ⚠️  线上 ID 可能元数据不齐
```

---

## 四、效果 / 结果相关

### Q13. 「我今天很开心」第一名不是「心情很不错」？
常见的几个根因（按概率从高到低）：
| 根因 | 怎么确认 | 修复 |
|------|----------|------|
| 模型不是 zh（中文版） | `ls BAAI/bge-small-zh-v1.5/` 看 `README.md` 标题 | 重新下载 `-zh-v1.5` 版 |
| query 没加 instruction | 打印 `query_with_instruction`，确认前缀完整 | 加 instruction |
| 小模型随机性 | 重复跑 3 次，看是否稳定 | bge-small 是 deterministic 的，应稳定；不稳定则可能 `normalize` 没开 |
| 相似度差距很小（<0.05） | 打印 3 个 score 具体数值 | 属正常，换 `bge-base-zh-v1.5` 可拉开差距 |

### Q14. 实际生产用，相似度阈值设多少合适？
以下是**中文 FAQ/知识库召回**场景的经验值（针对 bge-small-zh-v1.5）：

| 阈值 | 召回含义 | 建议用途 |
|------|----------|----------|
| ≥ 0.72 | 极高置信度，基本是「同一个问题的不同说法」 | 自动回答 / 无需人工兜底 |
| 0.58 ~ 0.72 | 高置信度，相关但可能需要重排序 | RAG Top-K 候选 |
| 0.40 ~ 0.58 | 中置信度，弱相关 | 可以让 Reranker 再挑一次 |
| < 0.40 | 大概率噪声 | 直接过滤，threshold = 0.40 即可 |

建议初始上线就用 `threshold=0.40, top_k=5`，再用真实数据迭代。

---

## 五、进阶 / 扩展相关

### Q15. 不升级硬件，还能怎么提升效果？
成本从低到高：
1. **换 base / large 版**：`bge-base-zh-v1.5`（768 dim）→ M 系列仍能跑；`bge-large-zh-v1.5`（1024 dim）推荐 16GB 内存。
2. **重排序（Reranker）**：召回 Top 20 后再过一遍 `bge-reranker-v2-m3`，NDCG@1 能提 5~10 个点。
3. **混合检索**：BM25（用 rank-bm25 库）+ 向量 0.5/0.5 加权融合，长尾关键词召回显著提升。
4. **Instruction Tuning 微调**：用你领域内的 (query, positive_doc) 对再训 1 epoch。

### Q16. 百万文档规模下 NumPy 全量点积太慢，下一步换什么？
**无缝升级路线（数学公式不变，只换存储层）**：
- **本地 100w 以内** → [FAISS](https://github.com/facebookresearch/faiss) `IndexFlatL2`（暴力）→ `IndexIVFPQ`（压缩 + 分桶）
- **需要服务化 / 持久化** → [Milvus Lite](https://github.com/milvus-io/milvus-lite) 单文件嵌入版
- **已有 PostgreSQL** → `pgvector` 扩展
- **云原生分布式** → Milvus / Qdrant / Weaviate / Elasticsearch 8.x dense_vector

---

## 六、环境 / 沙盒相关

### Q17. IDE 终端里报错但真正的 Terminal.app 里不报错？
**大概率是沙盒权限差异**：IDE 内嵌终端对家目录写入、网络代理有时会加限制。
**建议**：模型下载这一步，**优先用原生「终端.app」**运行；模型下载完之后，回到 IDE 里跑 02 / 03 脚本就没任何问题了。

### Q18. 想彻底干净地重来一次？
```bash
cd /Users/philipclaw/Downloads/padow-ai/Week5/bge-retrieval-lab
rm -rf BAAI .cache
python 01_下载模型.py
python 02_最小检索Demo.py
```
