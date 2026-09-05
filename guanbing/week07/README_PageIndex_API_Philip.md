# PageIndex_API_Philip.py · 使用说明

> 配套脚本：[PageIndex_API_Philip.py](file:///Users/philipclaw/Downloads/padow-ai/Week7/Week07/PageIndex_API_Philip.py)

---

## 一、概述

本脚本是 **VectifyAI 官方 PageIndex SDK（`pip install pageindex`）的 Philip 封装版**，完全基于「Local 本地模式」运行（不上传 PageIndex 云端），使用你同目录 `.env` 中已配置的 **DeepSeek / 通义千问 API Key** 通过 LiteLLM 路由层调用模型，完成 PDF 文档的：

1. **构建（Submit）**：Flash 索引模式下，用便宜模型（DeepSeek V4-Flash）把 PDF 生成分层的 `tree.json` 树状索引（Vectorless / Reasoning-based RAG 范式）
2. **检索与问答（Chat）**：用更准的模型（DeepSeek V4-Pro）自上而下「像人翻书一样 reasoning 遍历索引树」得到答案，引用精确到页

设计原则严格遵循你偏好的：**最小侵入式、.env 管理一切敏感信息、SCRIPT_DIR 绝对路径自动定位保证跨目录运行安全、先省费复用后再决定是否构建付费索引**。

---

## 二、环境 & 依赖

### 2.1 已验证 Python 包（主 `.venv` 已全部装好，无冲突 ✅）

| 包 | 版本 | 作用 |
|---|---|---|
| `pageindex` | 0.2.14 | 官方 SDK（Local 模式：不传 `api_key` 即走本地；传 `PAGEINDEX_API_KEY` 即云端）|
| `litellm` | 1.99.0 | Local 模式必需——PageIndex LocalAPI 内部通过 LiteLLM 路由到「`deepseek/…` / `qwen/…`」前缀的模型名，自动读对应 env var |
| `python-dotenv` | 1.2.2 | 加载 `.env` |
| `sentence-transformers / torch / elasticsearch …` | (Week6-7 项目通用依赖)| 本脚本不直接使用，但与 PageIndex 0.2.14 / LiteLLM 1.99.0 **零版本冲突** |

> **最小侵入安装记录（已完成）**：
> ```bash
> # pageindex 最小侵入装法，不碰你现有包
> pip install --no-deps pageindex==0.2.14
> # LiteLLM Local 模式必装（我们已经是 1.99.0，完全满足 >=1.97）
> pip install 'litellm>=1.97,<2'
> ```

### 2.2 `.env` 配置（自动读同目录 `.env`）

对应代码：[`load_env_and_validate()` #L25-L98](file:///Users/philipclaw/Downloads/padow-ai/Week7/Week07/PageIndex_API_Philip.py#L25-L98)

`.env` 模板示例（只需改 `DEEPSEEK_API_KEY` 为你的真实值）：

```dotenv
# 选择默认使用的模型：deepseek 或 qwen
LLM_PROVIDER=deepseek

# DeepSeek
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-v4-flash   # 本脚默认 Index=Flash / Chat=(Pro 若 .env 没填就用 Pro)

# Qwen（阿里云 DashScope 兼容模式；LLM_PROVIDER=qwen 时才需要）
QWEN_API_KEY=你的_Qwen_API_KEY
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus
```

### 2.3 硬约束 · 踩过的坑（已修复 ✅）

⚠️ **PageIndex 0.2.14 `client.py` L391 强约束**：`storage_path=` 和 `index=` 两个参数**互斥**，不能同传——否则直接抛 `PageIndexAPIError: index= and the flat index-side arguments (storage_path) are two spellings of the same thing`。

本脚本已修复：用顶部 [`SCRIPT_DIR` + `os.chdir(SCRIPT_DIR)` #L16-L17](file:///Users/philipclaw/Downloads/padow-ai/Week7/Week07/PageIndex_API_Philip.py#L16-L17) 保证 `.pageindex/` 目录始终写到 Week07 下，然后**初始化 Client 时只传 `index=...` & `chat=...`（不传 storage_path）**，完美绕开这个坑。

### 2.4 安全校验 3 条（`load_env_and_validate()` 里实现）

- `.env` 文件不存在 → `FileNotFoundError` 附模板
- `LLM_PROVIDER` 非 deepseek/qwen → `ValueError`
- API Key 为空 / 以 `你的_` 开头 / 占位符 `sk-xxx` → `RuntimeError`，明确告诉你该改哪一项

---

## 三、脚本固定内容 vs 自定义 / 泛化内容（你的要求 #1）

> **目的**：让你一眼看懂「哪些东西是框架级不用改的」「哪些是你做新项目/换模型/换供应商需要改的」

### 3.1 🔒 固定内容（通用框架，99% 场景无需修改）

| 模块 | 代码位置 | 为什么是固定内容 |
|---|---|---|
| **SCRIPT_DIR 路径定位 & `os.chdir`** | #L16-L22 | 保证跨目录运行（从 /tmp 或家目录调用脚本）也能找到 `.env` / PDF / `.pageindex/` |
| **`load_env_and_validate()` 环境加载 + 3 条安全校验** | #L25-L98 | 固定流程：dotenv → provider 分发 → setdefault 进 os.environ 供 LiteLLM 读；3 条校验防止静默失败 |
| **`print_tree_structure()` 递归打印树结构** | #L102-L125 | 通用 JSON 树可视化工具，与文档/模型/供应商无关 |
| **`list_documents()` 本地清单读取**（`manifest.json` 官方格式适配）| #L127-L147 | 读取官方 `{"docs": {id: {id,name,status,pageNum,createdAt,mode}}}` 格式，结构稳定不用改 |
| **`ensure_pdf_exists()` + `submit_and_wait()` 构建流程** | #L150-L184 | 固定：成本预估打印 → 提交 → 计时 → 打印树；`client.submit_document(pdf, wait=True)` 是 SDK 固定 API |
| **`run_chat_loop()` 3 道 BENCH 题 + 交互/非交互双模式** | #L194-L238 | 固定：ans → text 多格式兜底（str 或 dict 或嵌套 message）；单问题退出 or 交互式 CLI |
| **`main()` argparse 5 个参数分发** | #L240-L293 | 固定：`--list / --submit / --pdf / --doc-id / --question` 5 大模式分发；异常统一 exit 2 |
| **初始化 Client 不传 `api_key` → 强制 Local 模式** | #L255-L258 | 硬编码固定；保证**永远不会意外走 PageIndex Cloud 收费服务**（你没申请 PAGEINDEX_API_KEY 的情况下也不会报错）|

### 3.2 ✏️ 自定义 / 泛化内容（改这些 = 适配新文档/新模型/新场景）

| 模块 | 代码位置 | 什么时候需要改 | 修改建议 |
|---|---|---|---|
| **`DEFAULT_PDF` 测试 PDF 路径** | #L21 | 测试资料从 GraphRAG 论文换为合同/手册/年报时 | 改绝对路径或传入 `--pdf` CLI 参数（推荐，不碰代码）|
| **`REUSE_DOC_ID_DEFAULT` 默认复用 doc_id** | #L22 | 你构建了新文档（比如汽车手册），希望默认问答就走那个索引时 | 从 `--list` 输出里复制新的 doc_id 替换 |
| **`cfg_map` 供应商模型映射（index=便宜模型 / chat=贵模型）** | #L39-L56 | (a) 新增供应商（如 `openai` / `anthropic` / `bedrock/`）；(b) 想把 Chat 默认从 Pro 换为 Flash 省成本时 | 按 `{key_var, base_var, model_var, prefix, default_index, default_chat}` 六元组结构新增一条即可，下面的分发逻辑完全通用 |
| **索引模型策略（README Model Recommendations）** | #L79-L84 决定 `index_model` / `chat_model` 组合 | README 官方建议：Index 用便宜的、Chat 用准的 | 想省 Index 构建费就把 `default_index` 换成更便宜的 Flash/Plus；想省 Chat 费就改环境变量里 `DEEPSEEK_MODEL=deepseek-v4-flash` |
| **`BENCH_QUESTIONS` 预热 3 题** | #L187-L191 | 从 GraphRAG 论文切换到金融/法律/汽车手册时，把 3 道预热题换成对应领域典型题 | 对应替换成你的领域金标题，CLI 第一次进交互就直接跑 3 题验证 |
| **跨供应商 LiteLLM 前缀约定**（`deepseek/`、`qwen/`）| #L44 / #L52 / #L80 / #L83 | 新增供应商前缀格式遵循 LiteLLM docs：如 `bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0` | 只要按 LiteLLM 约定前缀写，本脚的 env var 注入 + 模型名拼接逻辑 100% 通用不用改 |
| **异常兜底行为**（`submit_and_wait()` L182 / `ask()` L213）| #L182 / #L213 | 从「教学容错」改「生产严格」时想让 CE 报错直接崩而不是 continue | 删掉 try/except 或 raise 自定义异常 |

---

## 四、脚本 5 大功能模式（复制命令直接用）（你的要求 #3）

### 4.1 ① 列本地已构建文档清单（免费 ✅ · 零 LLM 调用）
```bash
cd /Users/philipclaw/Downloads/padow-ai/Week7/Week07
source ../../.venv/bin/activate
python PageIndex_API_Philip.py --list
```
输出示例：
```
📚 本地已构建文档数：2
    1. [completed] pi-bb831222ab83… '2404.16130v2-GraphRAG.pdf'  页数=26  mode=flash  ⭐ 默认复用
    2. [completed] pi-002a0601a063… '2404.16130v2-GraphRAG_1.pdf' 页数=26  mode=flash
```

### 4.2 ② 省费交互式问答（默认行为 · 用 ⭐ 默认复用 doc_id，**不产生构建费**）
```bash
python PageIndex_API_Philip.py
```
- 自动先跑 [`BENCH_QUESTIONS` 3 道预热题](file:///Users/philipclaw/Downloads/padow-ai/Week7/Week07/PageIndex_API_Philip.py#L187-L191)
- 再进入 CLI 交互循环：输入问题回车回答，输入 `q / quit / exit / 空` 退出

### 4.3 ③ 非交互单问题（脚本化 / CI 用 · 答完就退出）
```bash
python PageIndex_API_Philip.py --question "GraphRAG的Map阶段和Reduce阶段分别做什么？"
```

### 4.4 ④ 指定 doc_id 问答（跳过默认 ⭐ 复用途径）
```bash
python PageIndex_API_Philip.py --doc-id pi-002a0601a06347d5873cc4de74bd0ac8
# 可叠加 --question 单问题退出
python PageIndex_API_Philip.py \
  --doc-id pi-002a0601a06347d5873cc4de74bd0ac8 \
  --question "GraphRAG实验对比了哪些baseline？指标上谁显著领先？"
```

### 4.5 ⑤ 构建新 PDF 索引（**会产生少量 LLM 构建费**，约 ¥0.008/页 DeepSeek Flash）
```bash
# 构建默认测试 PDF = GraphRAG 26 页论文 → 构建完进入交互问答
python PageIndex_API_Philip.py --submit

# ↓ 或 ↓ 自定义 PDF
python PageIndex_API_Philip.py --submit \
  --pdf "/Users/philipclaw/Downloads/padow-ai/Week6/Week06/汽车知识手册.pdf"
# 可叠加 --question 构建完答 1 题退出
```

构建时打印成本预估（你可以先看成本再决定是否 Ctrl+C）：
```
💰 官方参考成本 ≈ $0.0011 / 页（gpt-5.6-luna）→ 26 页 ≈ $0.029 ≈ ¥0.21
⏱  预期耗时：26 页 ≈ 30-60 秒
```

---

## 五、和教学版 08_PageIndex.py 的横向对比（教育价值总结）（你的要求 #2）

教学版脚本：[08_PageIndex.py](file:///Users/philipclaw/Downloads/padow-ai/Week7/Week07/08_PageIndex.py)

| 维度 | **教学版** 08_PageIndex.py（平级 5 页一节点）| **官方版** PageIndex_API_Philip.py（SDK 分层树）|
|---|---|---|
| **核心索引结构** | 平级节点：约 6 张卡片（`pages_per_node=5` 固定分块）| **分层嵌套树**：根 → 父节点 → 子节点 → 叶节点；如 `Methods → Workflow / Question-Gen / Evaluation` 3 个子节点 |
| **数据持久化** | ❌ 脚本退出就丢（学习笔记 §7 建议加缓存）| ✅ **`.pageindex/` 三件套**：`manifest.json` + `docs/<doc_id>/{doc.json, pages.json, tree.json}`；**构建一次永久复用**，后续问答零构建费 |
| **PDF 解析方式** | `pdfplumber` 纯文本提取（简单、可控、无外部依赖）| **Flash 模式内置 OCR + Markdown 抽取**（标题层级、表格、列表识别更精准，直接喂给 LLM 做分层摘要）|
| **检索 / 路由范式** | LLM 从「N 个平级节点摘要 + 页码」JSON 里 `node_id` 多选返回；结构简单好理解 | LLM 自上而下 **Reasoning 遍历树**（官方 "Reads Like a Human" 核心）——先看根摘要判断走哪个大章节 → 再钻进子节点，完全模拟人类翻专业书的行为 |
| **模型接入方式** | 原生 `requests.post` 直连 DeepSeek/Qwen OpenAI 兼容 HTTP；自己写 5 次重试 + 20s 超时 | 通过 **LiteLLM 路由层**：写 `deepseek/deepseek-v4-flash`、`bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0` 这种「供应商/模型」前缀即可自动读对应 env var + 处理端点；天然支持 100+ 供应商扩展 |
| **索引模型 × 问答模型策略** | 单模型统一（用哪个 provider 就是同一个 model 既建又答）| ✅ 严格遵循 README **Model Recommendations**：`index=` 用便宜模型做摘要，`chat=` 用你能负担的最准模型做 reasoning 翻树——省钱又精准 |
| **引用 / 可解释性粒度** | 页级（通过返回 node_id 反查 page_start~page_end）| 页级；升级 Cloud 可到行级 + 图片理解 |
| **适用场景** | 🔹 学习 PageIndex 5 段核心原理（索引/检索/路由/融合/拒答）<br>🔹 教学 Demo / 快速原型验证 | 🔹 生产级 RAG（合同/财报/研报/法规 10–1000 页）<br>🔹 真正体会「Vectorless + Reasoning-based」 vs Week6 「BM25 + BGE + RRF + CrossEncoder」的范式差异<br>🔹 后续 Week7 [12_GraphRAG.py](file:///Users/philipclaw/Downloads/padow-ai/Week7/Week07/12_GraphRAG.py) GraphRAG 的前置工程（分层摘要树 + 实体图谱 = GraphRAG 完整形态）|
| **教育上的互补** | 带你看透「LLM 三职一体 = Indexer + Router + Generator」——5 段代码一行行对应原理 | 让你真正接触到「工业级分层树索引」的构建耗时、成本规模、Manifest 管理、跨供应商切换等实际工程问题 |

### 教育路径建议（结合你 Week5-6-7 进度）

```
Week5 传统向量 RAG（BGE 512d + 切 40char 碎片）
    ↓  痛点：并列清单被切碎 / 上下文割裂
Week6 多路召回 + 精排（BM25 ∪ BGE → RRF → Cross-Encoder Top3）
    ↓  痛点：还是 similarity ≠ relevance；对"全局 sensemaking"类问题天然弱
Week7 08_PageIndex.py 教学版（平级 LLM 路由）
    ↓  痛点：平级结构在 100+ 页文档时路由不准 / 无持久化
  🌟 你现在的位置 🌟
Week7 PageIndex_API_Philip.py 官方 SDK 版（分层持久化树）
    ↓  下一步
Week7 12_GraphRAG.py（在分层摘要树上叠加实体-关系-声明三层知识图谱 + Leiden 社区）
```

---

## 六、数据存储目录结构（`.pageindex/`）

```
/Users/philipclaw/Downloads/padow-ai/Week7/Week07/.pageindex/
├── manifest.json                                           # 文档总清单（list_documents 读取来源）
└── docs/
    ├── pi-bb831222ab834527829560b6d7a468bc/               # doc_id 1（8/30 旧构建 ⭐ 默认复用）
    │   ├── doc.json                                        # 文档元信息：名称/描述/26页/status=completed
    │   ├── pages.json                                      # 26 页 OCR markdown：[{page_index, markdown}, ...]
    │   └── tree.json                                       # ★ 分层 PageIndex 树（嵌套 children 结构）
    └── pi-002a0601a06347d5873cc4de74bd0ac8/               # doc_id 2（9/5 新构建）
        └── doc.json / pages.json / tree.json
```

---

## 七、已验证输出（VERBATIM 结果样本）

### 7.1 省费问答（②，第 1 道 PageIndex vs Vector RAG）
> 用时 **11.7 s**，未花构建费（复用 ⭐ 默认 doc_id）
```
1) 索引结构不同：向量索引 vs 实体知识图谱
2) 支持的查询能力不同：局部事实检索 vs 全局综合理解（sensemaking）
3) 答案质量与查询成本表现不同：GraphRAG 全面性/多样性显著胜；Vector RAG 仅 directness 指标占优
```

### 7.2 新构建 + GraphRAG Workflow 题（⑤ + ③）
> 构建耗时 **81.8 s**，问答 **9.5 s**
```
索引期 5 步：① 切块  ② LLM 抽取实体/关系/声明  ③ 聚合为知识图谱  ④ Leiden 分层社区检测  ⑤ 自底向上社区摘要
查询期 2 步：⑥ Map 阶段（并行生成社区答案 + helpfulness 评分过滤 0 分）
          ⑦ Reduce 阶段（按 helpfulness 降序塞窗口 → 全局答案）
```

---

## 八、常见问题（FAQ）

### Q1. 为什么不传 `PAGEINDEX_API_KEY` 也能跑？
A. 我们用的是 **Local 模式**：PageIndex 构造器检测到 `api_key` 为空 → 内部走 `LocalAPI`（源码 client.py L460-L484），索引/检索/问答全在你本地跑，模型调用通过 LiteLLM 走你自己的 API Key，**完全不碰 VectifyAI 云端**。想切换 Cloud 模式的话：
```bash
# .env 里加一行 PAGEINDEX_API_KEY=你的官方云key
# 再把 PageIndexClient 初始化（#L255）改：
client = PageIndexClient(index="cloud", chat=chat_model)   # index="cloud" 就切云端构建
```

### Q2. 构建时报「同名文件重名 conflict → 自动存为 `xxx_1.pdf`」警告正常吗？
A. 正常（是我们 Step5 新构建时实际触发的 Warning）。PageIndex Local 模式为了避免覆盖你已构建的 doc_id，对同名 PDF 自动加 `_1/_2` 后缀，不影响使用；你可以之后用 `--doc-id` 任意切换。

### Q3. 想彻底删除某个 doc 的构建数据清空间怎么办？
A. 直接删 `.pageindex/docs/<doc_id>/` 文件夹 + 把 `manifest.json` 的对应 `docs.<doc_id>` 键删掉即可（JSON 格式保持合法）。

### Q4. 想把 Week6 354 页 `汽车知识手册.pdf` 也建一个 PageIndex 索引大概花多少钱？
A. 按 DeepSeek V4-Flash 约 ¥0.008 元/页：
   - 354 页 ≈ **¥ 2.8**（一杯奶茶钱）
   - 构建时间参考 README：26 页 → ~1min，354 页 → 约 **10–15 分钟**

---

## 九、复现 / 一键命令（全流程 1 行）

```bash
cd /Users/philipclaw/Downloads/padow-ai/Week7/Week07 && \
source ../../.venv/bin/activate && \
python PageIndex_API_Philip.py --list && \
echo "=== 复用上一个索引跑 3 道预热题 ===" && \
python PageIndex_API_Philip.py
```

（Ctrl+C 随时退出交互循环。需要构建新 PDF 时把最后一行换成 `python PageIndex_API_Philip.py --submit --pdf "你的路径.pdf"` 即可）
