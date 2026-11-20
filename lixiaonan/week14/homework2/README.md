# 基于 Kafka 的多模态 RAG 问答系统

需求见 `readme.txt` 与 `需求文档.md`（代码注释中的 F1-x/F2-x/F3-x 编号与之一一对应）。

## 目录结构

```
homework2/
├── readme.txt                 # 原始需求
├── 需求文档.md                 # 需求文档（评审基线）
├── README.md
├── docker-compose.yml         # 基础设施：Kafka(KRaft) / MySQL / MinIO / Qdrant
├── .env.example               # 环境变量模板（复制为 .env）
├── docs/                      # 设计/部署文档（M5 补全）
├── scripts/
│   ├── init_kafka_topics.py   # 创建 pdf-parse-task Topic
│   ├── init_db.py             # 建表（§4 数据模型）
│   └── run_dev.sh             # 一键启动开发环境
├── data/                      # 本地运行时数据（git 忽略内容）
│   ├── uploads/               # PDF 原件
│   ├── markdown/              # MinerU 产出的 Markdown
│   ├── images/                # 解析出的图片
│   ├── vector_store/          # faiss 本地模式数据
│   └── logs/
├── backend/
│   ├── requirements.txt
│   ├── app/                   # API 服务（FastAPI）
│   │   ├── main.py            # 应用入口
│   │   ├── core/              # 配置 / 日志 / 异常
│   │   ├── api/v1/            # documents / chat / conversations 路由
│   │   ├── db/                # SQLAlchemy 模型（§4）
│   │   ├── schemas/           # Pydantic DTO
│   │   ├── services/          # 上传编排 / 存储 / 检索 / 问答 / LLM 客户端
│   │   ├── mq/                # Kafka Producer + 消息结构（§3.1.3）
│   │   └── utils/             # 文件校验判重 / Markdown 工具
│   ├── worker/                # 解析 Worker（独立进程，消费 Kafka）
│   │   ├── main.py            # 消费入口：python -m worker.main
│   │   ├── consumer.py        # 手动提交 / 重试 / 状态流转（F2-3）
│   │   ├── parser/            # 解析器抽象 + MinerU 实现（F1-7）
│   │   ├── indexer/           # 分块 / Embedding / 向量库写入（F1-8）
│   │   └── captioner.py       # 图片描述生成（可选）
│   └── tests/
└── frontend/                  # Vue3 + Element Plus + Vite
    ├── package.json / vite.config.js / index.html
    └── src/
        ├── main.js / App.vue
        ├── router/            # /documents、/chat
        ├── api/               # 接口封装（§5）
        ├── views/             # DocumentListView（功能1+2）、ChatView（功能3）
        ├── components/        # StatusTag、MarkdownRenderer
        └── styles/
```

## 快速开始

```bash
# 1. 基础设施
docker compose up -d

# 2. 后端
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install "mineru[core]"        # MinerU（CPU 模式可运行，见需求文档开放问题4）

# 3. 环境变量与初始化
cp ../.env.example ../.env        # 按需修改 LLM_API_KEY 等
python ../scripts/init_kafka_topics.py
python ../scripts/init_db.py

# 4. 启动（一键：init + API + Worker + 前端）
bash ../scripts/run_dev.sh
# 或分进程启动 API / Worker / 前端：
#   uvicorn app.main:app --port 8000     （backend 目录）
#   python -m worker.main                （backend 目录）
#   npm run dev                          （frontend 目录，http://localhost:5173）

# 5. 运行测试
cd backend && python -m pytest tests -v
```

## 功能 ↔ 模块映射

| 需求 | 模块 |
| --- | --- |
| 功能1 上传+Kafka+MinerU 解析+存储 | `app/api/v1/documents.py`、`app/mq/`、`worker/`、`app/services/storage_service.py` |
| 功能2 解析状态展示 | `app/db/models.py`(状态机)、`worker/consumer.py`、`frontend/.../DocumentListView.vue` |
| 功能3 多模态问答 | `app/api/v1/chat.py`、`app/services/retrieval_service.py`、`chat_service.py`、`worker/indexer/`、`frontend/.../ChatView.vue` |

## 当前进度

- ✅ **M2 已完成并验证**（功能1 后端闭环 + 功能2 状态机后端）：
  - 批量上传 → 存原件 → 落库 PENDING → Kafka（acks=all，手动提交 offset）
  - Worker 消费 → PARSING → 解析 → Markdown/图片落盘 → 分块入库 → SUCCESS
  - 失败重试：指数退避重试 3 次后置 FAILED 并记录原因；`POST /{id}/reparse` 可重新入队
  - 已验证：双文档解析成功、损坏 PDF 重试耗尽进 FAILED、非法类型/超量/判重拦截、静态图片 HTTP 访问
  - 测试工具：`scripts/make_test_pdfs.py` 生成含图测试 PDF
- ✅ **M3 已完成并验证**（功能2 前端可视化 + 功能1 上传界面）：
  - `DocumentListView.vue`：拖拽多选上传（仅 .pdf，展示条数/大小限制）+ 状态表格（状态标签/页数/耗时/失败原因）+ 3 秒轮询 + 分页
  - 状态可视化：PENDING 待解析 / PARSING 解析中(带 loading 图标) / SUCCESS 解析完成 / FAILED 解析失败
  - 操作：查看解析结果（Markdown 图文预览，marked + DOMPurify 渲染）、失败文档一键重新解析
  - 已验证：vite 代理上传 → Kafka → Worker 解析 → 页面轮询可见 SUCCESS/FAILED 全状态
- ✅ **M4 已完成并验证**（功能3 多模态问答，已接真实 LLM）：
  - 检索（F3-3）：bge-small-zh-v1.5 向量化（GPU 可用），FAISS IndexIDMap2 存储，文本块+图片块（周边上下文/可选 LLM 描述）全部入索引，支持按文档过滤；未开向量化时自动降级关键词检索
  - 问答（F3-4~F3-8）：`POST /api/v1/chat` SSE 流式（meta/status/chunk/done/error 事件）；多模态 Prompt 组装（资料图片与用户图片均转 data URL 送 LLM）；回答 Markdown 图文混排 + 来源引用
  - 会话（F3-7）：conversation/message 持久化，多轮上下文（最近 6 条），会话列表/历史消息接口
  - 前端 `ChatView.vue`：会话侧栏 + 流式渲染 + 图问上传（≤3 张 base64）+ 检索范围选择 + 停止按钮
  - 已验证：文本问答（语义命中 0.61）、图问（图片转存 + 持久化 + 事件序列完整）、会话历史
  - **LLM 接入**：`.env` 中 `LLM_BACKEND=openai` + `LLM_API_KEY/LLM_API_BASE/LLM_MODEL` 即可切换真实多模态模型（默认智谱端点，兼容 dashscope/vLLM 等）
- **SQLite 注意**：多进程并发写已加 busy_timeout=30s；生产/演示建议切 MySQL（改 `DATABASE_URL` 即可）
- ✅ **M5 已完成并验证**（真实 LLM 联调 + 测试 + 文档 + 兜底）：
  - **真实 LLM 已接入**：智谱 `glm-5.3-flash`（OpenAI 兼容协议），文本问答、图片召回、图问全部用真实模型验证
  - 思考模型适配：`LLM_THINKING_EFFORT=low`（low/high/max，非思考模型置空）；流式仅转发 `delta.content`、reasoning 增量自动跳过；`LLM_MAX_TOKENS=4096` 防止被 reasoning 耗尽
  - 图片描述生成已开启（`EMBEDDING_IMAGE_CAPTION=true`，§7 开放问题 2 定案：启用），含图 PDF 解析时自动生成中文描述入向量
  - E2E 验证：文本问答流式返回 Markdown+引用；提问「示意图颜色」命中 IMAGE 块（score 0.58 居首）并在回答中以 `![](/static/images/...)` 引用；图问上传蓝色圆形 → LLM 正确识别并与知识库红色示意图对比，返回 5 条 citations
  - F2-4 已实现（可选功能）：`STALE_PARSING_MINUTES=30` 超时兜底，Worker 周期检查卡死的 PARSING 任务重新入队，二次超时置 FAILED 防死循环
  - 测试：`backend/tests/` 31 个用例（上传校验/判重/Kafka 失败置 FAILED/Worker 管道/重试/幂等/超时兜底/SSE 问答/Prompt 组装/会话），`pytest tests -v` 全部通过
  - 文档：`docs/设计说明.md`、`docs/部署文档.md`（部署/配置/验收自检/FAQ）；`scripts/run_dev.sh` 一键启动已实现

## 开发环境说明（本沙箱）

- 本机 `localhost:9092` 已有 Kafka broker（KRaft），无需 docker compose 即可联调
- **MinerU 4.1.7 已安装并复验通过**（`PARSER_BACKEND=mineru`）：4.x 需先 `mineru server start` 启动本地解析引擎（`parse_server.local.mode=managed`、档位 `basic`，模型从 ModelScope 下载约 5G），`mineru_parser.py` 已适配 4.x 产物形态（`doc:` 库内引用经 `mineru read` 导出图片、页标记归一为「第 N 页」标题）；实测含图 PDF 75s 解析成功、图片块页码正确、问答可命中引用
- 数据库/存储在沙箱使用 SQLite + 本地磁盘（`data/homework2.db`），交付环境可切 MySQL/MinIO
- 沙箱仅 4G 内存，MinerU standard 档（1.2B VLM）无法加载，使用 basic 档（onnx pipeline）；验收机 ≥8G 内存可升级 standard 档获得更好版面还原

## 技术选型说明

- 向量库默认 **Qdrant**（单容器轻量）；如选 Milvus 需追加 etcd 依赖，`worker/indexer/vector_store.py` 已按适配层设计，可切换。
- 存储默认本地磁盘（`data/`），`.env` 中 `STORAGE_BACKEND=minio` 可切换 MinIO。
- LLM 需配置为多模态模型（如 qwen2.5-vl），否则图问/图片召回描述无法工作。
