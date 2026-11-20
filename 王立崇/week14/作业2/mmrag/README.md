# MinerU × Kafka 多模态 RAG

一个可本地运行的文档问答 Demo：浏览器上传多个 PDF，经 Kafka 异步交给 worker，用 **MinerU** 做版面分析、OCR、表格与图片抽取；解析结果进入 **SQLite + FTS5** 索引。聊天侧同时检索文字、表格和插图，并调用本地多模态 **Qwen + vLLM**，返回带引用的 Markdown 图文混排回答。

## 功能

- **多 PDF 上传**：拖拽/多选上传，经 FastAPI 写入磁盘后投递到 Kafka `mmrag.ingest`。
- **异步解析**：worker 消费消息，调用 MinerU，抽取 Markdown、`structured_content.json`、表格、图片。
- **解析状态**：前端展示 `pending`（待解析）、`parsing`（解析中，带进度与阶段）、`completed`（解析完成）和 `failed`（解析失败）。
- **状态链路**：worker 将状态写入 SQLite，同时发送到 Kafka `mmrag.status`；API 消费后通过 SSE `/api/events` 推送给浏览器，另有轮询兜底。
- **多模态问答**：
  - 检索正文、表格、插图描述，回答使用 Markdown，支持表格与 `/media/...` 图片，形成图文混排。
  - 用户可在输入框附一张图片（或直接粘贴），图片会同时用于检索增强和发送给多模态 LLM。
  - 可勾选已解析文档缩小检索范围。
- **安全渲染**：前端 Markdown 渲染器手写实现，先转义 HTML，仅允许本地 `/media/...`、图片 data URL 和 http(s) 链接；图片可放大预览。

## 架构

```text
                         ┌──────────────────────────────┐
 Browser                 │ FastAPI :8080                │
  multi-PDF upload ─────►│ POST /api/upload             │
  status (SSE/poll) ◄────│ GET  /api/events             │◄──── Kafka mmrag.status
  chat + image ─────────►│ POST /api/chat               │
                         └──────────────┬───────────────┘
                                        │ Kafka mmrag.ingest
                                        ▼
                         ┌──────────────────────────────┐
                         │ worker                       │
                         │  MinerU CLI (basic tier)     │
                         │  └─ markdown / structured    │
                         │  VLM 图片描述 (vLLM)         │
                         │  chunk + jieba tokenization  │
                         │  SQLite FTS5                 │
                         └──────────────────────────────┘

 chat: question (+ optional image) → retrieval (BM25 + figure recap)
       → text/table chunks + up to 3 retrieved images + user image
       → multimodal LLM → Markdown answer + [n] citations + images
```

解析产物保存在 `data/parsed/<document_id>/`，其中 `images/` 由 `/media/<document_id>/images/...` 提供。上传的原始 PDF 在 `data/uploads/`。

## 环境依赖

| 组件 | 默认地址/路径 | 说明 |
| --- | --- | --- |
| Kafka | `localhost:9092` | 需要可用的 broker；API/worker 会自动创建 topic |
| vLLM | `http://localhost:8000/v1` | OpenAI 兼容接口，模型 id 默认 `models/Qwen3.5-0.8B/` |
| MinerU | `mineru-kit` | 默认 `basic` 档；见下方 GPU 说明 |

当前环境的 vLLM 与本项目默认使用 GPU，若同时启动 MinerU `advanced` 档，MinerU 内部的 vLLM 可能因显存不足失败。worker 会先尝试用户选择的档位，再回退到 `basic`。单机 Demo 建议保持 `basic`。

```bash
# 示例：检查依赖服务
curl http://localhost:8000/v1/models
/workspace/software/kafka_2.13-4.4.0/bin/kafka-topics.sh \
  --bootstrap-server localhost:9092 --list
```

## 启动

```bash
cd /workspace/mmrag
python -m pip install -r requirements.txt
cp .env.example .env   # 按环境修改；Kafka/vLLM 地址尤其重要

bash scripts/start.sh
# 浏览器打开 http://localhost:8080
```

脚本会启动 API 和 worker，PID 和日志放在：

```text
data/api.pid
data/worker.pid
data/logs/api.log
data/logs/worker.log
```

停止：

```bash
bash scripts/stop.sh
```

也可以手动运行：

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
python -m app.worker
```

首次请求 jieba 会花约 0.5 秒加载词典，之后正常。

只创建 Kafka topic（API/worker 通常也会自动创建）：

```bash
python scripts/topics.py
```

运行文本处理单元测试：

```bash
python -m unittest discover -s tests -v
```

## 使用

1. 左侧拖入一个或多个 PDF，选择解析档位，点击上传。
2. 观察文档卡片状态：**待解析 → 解析中 → 解析完成**；解析中会显示阶段和进度（MinerU 解析、理解插图、建立索引）。
3. 点击“解析完成”的文档可预览 Markdown 和抽取图像；勾选复选框可把问答限定在这些文档。
4. 在底部输入问题；点回形针选择图片，或直接把图片粘贴到输入框再发送。
5. 回答中的 `[n]` 引用对应“引用来源”里的资料卡片，点击卡片可打开文档预览。

## API

### 上传

```bash
curl -X POST http://localhost:8080/api/upload \
  -F 'files=@/workspace/pdf/test2.pdf;type=application/pdf' \
  -F 'tier=basic'
```

返回 `202`：

```json
{
  "accepted": 1,
  "documents": [{"id": "...", "filename": "test2.pdf", "status": "pending"}],
  "rejected": []
}
```

### 文档和状态

```bash
curl http://localhost:8080/api/documents
curl http://localhost:8080/api/documents/<document_id>
curl -N http://localhost:8080/api/events       # SSE
curl http://localhost:8080/api/health
```

### 文本问答

```bash
curl -X POST http://localhost:8080/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"question":"五种 QA 问答场景有什么区别？请用表格对比。"}'
```

### 图文问答

`image_data_url` 是浏览器自己压缩后的 JPEG data URL；也可以直接传 `data:image/png;base64,...`：

```bash
curl -X POST http://localhost:8080/api/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "这张图展示了什么？结合文档说明。",
    "image_data_url": "data:image/jpeg;base64,...",
    "doc_ids": ["<document_id>"],
    "history": []
  }'
```

响应包含 `answer`、`references`（带页码、标题、图像链接）、`query_tokens`、`query_image_description`、`latency_ms` 等字段。

## 目录

```text
app/
  main.py          FastAPI 路由、上传、SSE、chat
  worker.py        Kafka consumer + 解析任务
  ingestion.py     MinerU 调用、zip 安全解压、图片描述、落库
  chunker.py       structured_content → 文本/表格/图像 chunk
  retrieval.py     FTS5 BM25 + 图像兜底检索
  qa.py            检索增强 + 多模态回答编排
  llm.py           OpenAI 兼容客户端、图片压缩、VLM prompt
  kafka_bus.py     topic 创建、producer、状态事件 fan-out
  db.py            SQLite schema / 查询 / FTS5
  textutils.py     jieba、停用词、HTML 表格转文本
  static/          前端 HTML/CSS/JS
data/
  uploads/         原始 PDF
  parsed/<id>/     MinerU 产物、markdown、images
  mmrag.db         SQLite + FTS5
scripts/
  start.sh stop.sh topics.py
```

## 设计说明与限制

- **MinerU 失败回退**：`MINERU_TIER` 是首选档位，`MINERU_FALLBACK_TIER` 失败后再试，最后一定尝试 `basic`。每个文档的档位和错误会记录在 SQLite。
- **索引粒度**：文本/表格 chunk 约 850 字符；图像单独成 chunk，caption 来自 VLM。FTS5 使用 `jieba` 分词后的 token，查询按 `OR` 检索以适配中文口语问法。
- **图片理解**：解析阶段对插图调用 VLM 生成中文描述，描述进入检索文本；问答阶段最多把 3 张检索到的原图作为视觉上下文发送给 LLM。
- **引用与图片兜底**：如果小模型没有在 Markdown 中插入图片，`qa._ensure_visual_layout` 会追加“相关图示”，保证输出具有图文混排结构。
- **Demo 边界**：未实现登录/权限/多租户；SQLite 适合单机演示；上传文件会持久保存；错误信息可能包含本机路径。生产化时应换成对象存储、任务重试/幂等策略、更强的鉴权与内容审核。
- **状态可恢复性**：worker 手动提交 Kafka offset。解析过程中崩溃会重新投递，已完成的文档会被跳过；事件只用于实时 UI 更新，页面刷新后以 DB 为准。
