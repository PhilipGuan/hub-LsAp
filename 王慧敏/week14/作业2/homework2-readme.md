# 作业2：Kafka 多模态 RAG 问答

在 `readme.md` 中"Kafka 聊天消息管道"的解耦思想基础上，复刻一个完整的"多模态 RAG 问答"系统：
前端上传 PDF → Kafka 异步解耦 → MinerU 解析 → 向量入库 → 图文混排问答。

## 功能对照

| 作业要求 | 实现位置 |
|---|---|
| 功能1：前端上传多个 PDF，经 Kafka 到后端，MinerU 解析并存储 | `streamlit_app.py`（多选上传）→ `app/main.py`（落盘+发 Kafka）→ `app/worker.py`（消费+调用 `app/mineru_parser.py`）|
| 功能2：前端展示解析状态（待解析/解析中/解析完成） | `app/db.py`（SQLite 状态机）+ `streamlit_app.py` 每 2 秒自动 rerun 轮询 `GET /api/documents` |
| 功能3：多模态问答，回答图文混排 + 用户图问一起送 LLM | `app/vector_store.py`（图文配对分块+检索）+ `app/llm_client.py`（多模态消息组装）+ `app/rag_service.py` |

## 架构图

```
┌────────────┐  多选PDF   ┌─────────────┐  send_parse_task   ┌───────────┐
│  Streamlit  │ ─────────▶ │  FastAPI    │ ──────────────────▶│  Kafka     │
│streamlit_app│  HTTP      │  main.py    │   topic:            │ pdf-parse- │
└────────────┘            │ (写SQLite   │   pdf-parse-tasks    │ -tasks     │
      ▲   轮询状态/问答     │  待解析)    │                      └─────┬─────┘
      │                   └─────────────┘                            │
      │                                                                ▼
      │                                                        ┌───────────────┐
      │                                                        │  worker.py    │
      │                                                        │ (消费任务)     │
      │                                                        │ 状态→解析中    │
      │                                                        │ 调 MinerU 解析 │
      │                                                        │ 切分+写向量库  │
      │                                                        │ 状态→解析完成  │
      │                                                        └───────┬───────┘
      │                                                                ▼
      │                                                        ┌───────────────┐
      │          POST /api/chat                                │ Chroma 向量库  │
      └───────────────────────────────────────────────────────▶│ (文本+图片路径)│
                        检索 top_k → 组装多模态 prompt           └───────────────┘
                        → 调用 qwen-vl-plus（图文一起送）→ 图文混排返回
```

## 目录结构

```
app/
  config.py         配置（Kafka/SQLite/Chroma/LLM，全部走环境变量）
  db.py             SQLite：文档记录 + 解析状态机
  kafka_bus.py      Kafka 生产者/消费者封装（通用，复用 key=doc_id 保证有序）
  mineru_parser.py  调用 magic-pdf CLI 解析 PDF，返回 markdown + 图片路径
  vector_store.py   Chroma 向量库：markdown 按"图文配对"切块 + 向量检索
  llm_client.py     多模态 LLM 客户端（OpenAI 兼容接口，图片转 base64 内嵌）
  rag_service.py    RAG 主流程：检索 → 组装上下文 → 调 LLM → 组装图文混排结果
  worker.py         Kafka Worker 进程：消费解析任务，串联 mineru_parser + vector_store
  main.py           FastAPI 应用：上传 / 状态查询 / 问答接口
streamlit_app.py     Streamlit 前端：多选上传、状态轮询、图文混排对话框
data/                运行时生成：uploads/ parsed/ chroma/ app.db
requirements.txt
.env.example         环境变量模板，复制为 .env 后按需修改
```

## 前置依赖

1. **Kafka**：本地起一个 broker（默认 `localhost:9092`），并创建 topic：
   ```powershell
   kafka-topics --create --topic pdf-parse-tasks --bootstrap-server localhost:9092 --partitions 3 --replication-factor 1
   ```
2. **MinerU**：已安装 `magic-pdf` 命令行工具（`pip install magic-pdf[full]` 或参考官方文档），确保 `magic-pdf -v` 能正常执行。
3. **多模态 LLM**：默认接 DashScope 的 OpenAI 兼容接口，模型 `qwen-vl-plus`。复制 `.env.example` 为 `.env`，填入 `LLM_API_KEY`（DashScope 的 API Key）。

## 安装与运行

```powershell
pip install -r requirements.txt
copy .env.example .env   # 然后编辑 .env 填入 LLM_API_KEY

# 终端1：启动解析 worker（会自动建表）
python -m app.worker

# 终端2：启动后端 API
uvicorn app.main:app --reload --port 8000

# 终端3：启动 Streamlit 前端
streamlit run streamlit_app.py
```

默认会自动打开浏览器（一般是 `http://localhost:8501`），也可以手动访问该地址：
1. 上传若干 PDF → 表格实时显示"待解析 → 解析中 → 解析完成"（勾选"自动刷新"后每 2 秒轮询一次）
2. 解析完成后，在问答区勾选要检索的文档（不勾选=检索全部已解析完成的文档），在底部聊天输入框发问，也可以上传一张图片随问题一起发送
3. 对话区会用聊天气泡展示 LLM 文本回答，并把检索到的相关图片一起图文混排展示

> Streamlit 前端通过 `API_BASE` 环境变量（默认 `http://127.0.0.1:8000`）访问 FastAPI 后端，两者是独立进程，可以分别重启。

## 关键设计说明

- **为什么要走 Kafka 而不是同步解析**：PDF 解析（尤其 MinerU 的 OCR/版面分析）耗时且可能失败，用 Kafka 把"上传"和"解析"解耦，解析慢/挂掉不影响前端继续上传，worker 重启后凭 offset 还能继续处理，和 `readme.md` 中"Kafka 聊天消息管道"的解耦思路一致。
- **key = doc_id**：保证同一文档的后续消息（如重试）始终进同一分区，不会乱序。
- **图文配对分块**：MinerU 输出的 markdown 是图文混排的，`vector_store.split_markdown_with_images` 在切分文本块时，把图片紧邻的上下文文本和图片路径打包成一个 chunk 存进 metadata；检索命中该文本块时，连带检索出配套的图片，从而实现"检索到的文字资料里有图就一起带出来"。
- **图问一起送 LLM**：`llm_client.build_user_content` 把检索到的资料图片和用户自己上传的图片，都转成 `image_url`（base64 data URL）和问题文本一起放进同一条 `user` message 的 `content` 数组，符合多模态模型（如 qwen-vl-plus / gpt-4o）的标准输入格式。
- **回答图文混排**：后端把回答文本和检索到的图片（base64）一起返回，前端在同一个消息气泡里先渲染文字再渲染图片，实现展示层的图文混排。

## 已知简化（教学/作业场景取舍）

- Embedding 用本地 `sentence-transformers` 模型，避免再依赖一个 embedding API；首次运行会自动下载模型（需要联网）。
- 图片检索依赖"文本块关联图片"的间接策略，不是真正的图像向量检索（如 CLIP），足以满足"图文配对展示"的作业要求。
- 单机单 worker 示例；如需水平扩展，多开几个 `python -m app.worker` 进程即可（同一 `group.id` 自动分摊分区）。
