# 第十五周作业 2：基于 Kafka 的多模态 RAG 问答

> 本目录目前只保存实现大纲，不包含完整项目代码。  
> 需求来源：一次上传多个 PDF，经 Kafka 异步交给 MinerU 解析，再完成支持图文混排和图片提问的多模态 RAG 问答。

## 1. 最终效果

### 文档管理页

- 一次上传多个 PDF。
- 每个 PDF 独立显示解析状态：`待解析 → 解析中 → 解析完成`。
- 支持查看失败原因。
- 页面刷新后仍能查看历史文档及状态。

### 多模态问答页

- 支持纯文本提问。
- 支持“图片 + 文字”一起提问。
- 回答可以包含文字、PDF 原图、页码和文档来源。
- 回答按文字、图片、文字的顺序混排展示。

## 2. 技术选型

| 模块 | 推荐方案 |
| --- | --- |
| 前端 | Vue 3 + Element Plus，或 React + Ant Design |
| 后端 API | FastAPI |
| 异步消息 | 镜像中已有的 Kafka |
| PDF 解析 | MinerU |
| 文件存储 | 本地目录优先，进阶使用 MinIO |
| 任务状态 | SQLite 优先，进阶使用 PostgreSQL |
| 缓存 | Redis，可选 |
| 向量数据库 | Chroma / FAISS 优先，进阶使用 Qdrant |
| Embedding | BGE-M3 或其他中文文本向量模型 |
| 多模态模型 | Qwen-VL、InternVL、GLM-4V 等支持图片输入的模型 |
| 图片渲染 | Markdown + 结构化图片块 |

作业 MVP 可以全部运行在 CompShare 镜像内，只使用本地目录、SQLite、Chroma 和现有 Kafka。

## 3. 总体架构

```text
前端网页
  ├── 上传多个 PDF
  └── 文字/图片问答
        │
        ▼
FastAPI 后端
  ├── 保存上传文件
  ├── 创建文档任务
  ├── 查询解析状态
  └── 调用 RAG 问答服务
        │
        ├── Kafka ──> MinerU Worker
        │                 ├── 解析 PDF
        │                 ├── 保存 Markdown/图片
        │                 ├── 更新任务状态
        │                 └── 建立文本和图片索引
        │
        ├── SQLite/PostgreSQL：文档、任务、聊天记录
        ├── 本地文件系统/MinIO：PDF、解析结果、图片
        └── 向量数据库：文本块和图片块
                          │
                          ▼
                 多模态 LLM / VLM
                          │
                          ▼
              图文混排答案 + 来源页码
```

核心原则：

- 上传接口只负责接收文件并发送 Kafka 消息，不等待 MinerU。
- MinerU 解析放在独立 Worker 中执行。
- 状态以数据库为准，Kafka 负责异步任务和状态事件。
- RAG 只把检索到的 Top-K 文本和图片交给模型。
- 模型只能引用检索结果中真实存在的图片 ID。

## 4. Kafka 设计

### 4.1 Topic

| Topic | 作用 |
| --- | --- |
| `pdf.parse.requested` | 新 PDF 的解析请求 |
| `pdf.parse.succeeded` | MinerU 解析成功 |
| `pdf.parse.failed` | MinerU 解析失败 |
| `pdf.index.requested` | 解析完成后的索引请求，可选 |
| `task.status.changed` | 状态变化通知，可用于 SSE |
| `pdf.parse.dlq` | 多次失败后的死信队列，进阶功能 |

MVP 至少实现：

```text
pdf.parse.requested
pdf.parse.succeeded
pdf.parse.failed
```

### 4.2 解析请求消息

```json
{
  "task_id": "task-001",
  "doc_id": "doc-001",
  "filename": "机器学习.pdf",
  "file_path": "/workspace/data/uploads/doc-001.pdf",
  "attempt": 1,
  "created_at": "2026-11-13T10:00:00Z"
}
```

### 4.3 Kafka Key 和消费者组

- 消息 key 使用 `doc_id`，保证同一文档在同一分区内有序。
- MinerU Worker 使用消费者组 `mineru-workers`。
- 索引 Worker 使用消费者组 `index-workers`。
- 消费逻辑必须幂等：同一文档重复消费时先清理旧数据，再写入新数据。
- 失败任务记录重试次数，超过上限后发送到 DLQ。

## 5. 任务状态机

```text
PENDING      待解析：数据库记录已创建
QUEUED       已发送到 Kafka
PARSING      解析中：MinerU 正在处理
INDEXING     索引中：切块、生成向量、写入向量库
COMPLETED    解析完成
FAILED       解析失败
```

前端至少显示：

```text
待解析 → 解析中 → 解析完成
```

MVP 使用轮询：

```text
GET /api/documents
```

进阶使用 SSE：

```text
GET /api/events
```

## 6. 数据模型

### `documents`

```text
id
filename
original_path
status
error_message
page_count
created_at
updated_at
```

### `chunks`

```text
id
doc_id
chunk_type       text / image / table
text_content
caption
image_path
page_number
vector_id
```

### `chat_history`

```text
id
question
question_image_path
answer_json
sources_json
created_at
```

## 7. 功能 1：多 PDF 上传和 MinerU 解析

### 7.1 上传流程

```text
用户选择多个 PDF
→ 前端逐个或批量提交
→ 后端保存文件
→ 为每个 PDF 创建 doc_id
→ 写入 PENDING 状态
→ 向 Kafka 发送解析请求
→ 更新为 QUEUED
→ 立即返回 doc_id
```

上传接口不能同步执行 MinerU，否则多个 PDF 会阻塞请求。

### 7.2 推荐接口

```text
POST /api/documents/upload
Content-Type: multipart/form-data
```

### 7.3 MinerU Worker 流程

```text
消费 pdf.parse.requested
→ 更新状态为 PARSING
→ 调用 MinerU
→ 保存 Markdown、content_list.json、图片
→ 更新状态为 INDEXING
→ 切块并建立索引
→ 更新状态为 COMPLETED
→ 发送 pdf.parse.succeeded
```

失败时：

```text
捕获异常
→ 保存错误信息
→ 状态更新为 FAILED
→ 发送 pdf.parse.failed
```

### 7.4 解析结果目录

```text
/workspace/data/parsed/{doc_id}/
├── document.md
├── content_list.json
└── images/
    ├── page_1_fig_1.png
    └── page_3_table_1.png
```

MinerU 的 Markdown 用于文本检索，`content_list.json` 用于保留页码、图片路径和版面关系。

## 8. 功能 2：解析状态展示

### 8.1 文档列表字段

- 文件名
- 文件大小
- 当前状态
- 错误信息
- 创建时间
- 解析完成时间

### 8.2 状态颜色建议

```text
待解析    灰色
解析中    蓝色
索引中    橙色
解析完成  绿色
解析失败  红色
```

### 8.3 状态接口

```text
GET /api/documents
GET /api/documents/{doc_id}
```

前端 MVP 每秒轮询一次即可。作业演示不需要一开始就做 WebSocket。

## 9. 索引流程

### 9.1 文本切块

按 Markdown 标题、段落和页码切块，并保存：

- `doc_id`
- 页码
- 原文
- 标题路径
- 相邻图片路径

### 9.2 图片处理

MinerU 提取出的每张图片单独保存：

```text
image_id
doc_id
page
image_path
caption
surrounding_text
image_embedding
```

`caption` 可以用 VLM 生成，例如：

```text
第 3 页展示了神经网络的结构，包括输入层、隐藏层和输出层。
```

### 9.3 向量库记录

文本块：

```json
{
  "id": "chunk-001",
  "vector": [0.12, 0.34],
  "payload": {
    "doc_id": "doc-001",
    "type": "text",
    "page": 3,
    "text": "神经网络由输入层、隐藏层和输出层组成"
  }
}
```

图片块：

```json
{
  "id": "image-001",
  "vector": [0.22, 0.18],
  "payload": {
    "doc_id": "doc-001",
    "type": "image",
    "page": 3,
    "image_path": "/workspace/data/parsed/doc-001/images/page_3_fig_1.png",
    "caption": "神经网络结构图"
  }
}
```

## 10. 功能 3：多模态 RAG 问答

### 10.1 纯文本提问

```text
问题向量化
→ 检索相关文本块和图片块
→ 可选重排序
→ 组织上下文
→ 多模态 LLM 回答
→ 返回文字、图片和来源
```

### 10.2 图片 + 文字提问

```text
保存用户图片
→ VLM 分析用户图片
→ 生成图片描述
→ 图片描述 + 原问题组成检索查询
→ 检索文档中的文本和图片
→ 用户图片、检索图片、文本上下文一起送入 VLM
→ 返回图文混排答案
```

### 10.3 上下文格式示例

```text
【文本片段 1】
文档：机器学习.pdf，第 3 页
内容：神经网络由输入层、隐藏层和输出层组成……

【图片片段 1】
图片 ID：image-001
文档：机器学习.pdf，第 3 页
图片描述：神经网络结构示意图

【用户问题】
请结合文档解释这张图片中的网络结构。
```

### 10.4 图文混排输出协议

不要让模型自由生成图片 URL。让模型返回结构化 JSON：

```json
{
  "blocks": [
    {
      "type": "text",
      "content": "该图展示了一个三层神经网络结构。"
    },
    {
      "type": "image",
      "asset_id": "image-001",
      "caption": "图 3-1 神经网络结构示意图"
    },
    {
      "type": "text",
      "content": "输入层负责接收特征，隐藏层负责特征变换。"
    }
  ],
  "citations": [
    {
      "doc_id": "doc-001",
      "page": 3
    }
  ]
}
```

前端按 `blocks` 顺序渲染，并只接受检索结果中存在的 `asset_id`。

## 11. API 大纲

```text
POST /api/documents/upload
批量上传 PDF，返回 doc_id 列表

GET /api/documents
查询所有文档和解析状态

GET /api/documents/{doc_id}
查询单个文档状态和错误信息

GET /api/documents/{doc_id}/chunks
查看解析和切块结果，便于调试

POST /api/chat
提交文字问题或图文问题

GET /api/assets/{asset_id}
读取 PDF 中提取出的图片

GET /api/events
SSE 状态推送，进阶功能
```

## 12. 前端页面

```text
src/
├── pages/
│   ├── DocumentPage.vue      上传和文档状态
│   └── ChatPage.vue          多模态问答
├── components/
│   ├── DocumentUploader.vue
│   ├── DocumentStatusList.vue
│   ├── ChatInput.vue
│   ├── AnswerBlocks.vue
│   └── SourceList.vue
└── api/
    └── client.js
```

## 13. 推荐项目目录

```text
Week15_作业2_多模态RAG_Kafka/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── api/
│   │   │   ├── documents.py
│   │   │   └── chat.py
│   │   ├── kafka/
│   │   │   ├── producer.py
│   │   │   └── consumer.py
│   │   ├── workers/
│   │   │   ├── mineru_worker.py
│   │   │   └── index_worker.py
│   │   ├── services/
│   │   │   ├── mineru_service.py
│   │   │   ├── embedding_service.py
│   │   │   ├── vectordb_service.py
│   │   │   └── llm_service.py
│   │   └── models/
│   └── requirements.txt
├── frontend/
├── data/
│   ├── uploads/
│   ├── parsed/
│   └── chat_images/
├── .env.example
└── README.md
```

## 14. 实现顺序

### 第一阶段：上传和 Kafka

1. 搭建 FastAPI。
2. 实现多 PDF 上传。
3. 保存文件并创建文档记录。
4. 向 Kafka 发送解析请求。
5. 用生产者和消费者验证消息链路。

### 第二阶段：MinerU 和状态

1. 实现 MinerU Worker。
2. 保存 Markdown、JSON 和图片。
3. 更新 `PARSING`、`COMPLETED`、`FAILED`。
4. 前端轮询并展示状态。

### 第三阶段：文本 RAG

1. Markdown 切块。
2. 生成文本 Embedding。
3. 写入向量库。
4. 实现 Top-K 检索。
5. 调用 LLM 回答并返回页码来源。

### 第四阶段：多模态

1. 保存 MinerU 图片。
2. 生成图片 Caption。
3. 图片块写入向量库。
4. 支持用户上传图片提问。
5. 将用户图片和检索图片一起送入 VLM。
6. 返回可渲染的图文混排 `blocks`。

### 第五阶段：演示和提交

1. 上传 3 个 PDF。
2. 展示状态变化。
3. 展示 Kafka 消息和 offset。
4. 展示 MinerU 生成的 Markdown 和图片。
5. 演示纯文本 RAG。
6. 演示图片 + 问题。
7. 展示带原图和页码的回答。

## 15. 验收清单

- [ ] 一次上传多个 PDF。
- [ ] 每个 PDF 独立显示 `待解析/解析中/解析完成`。
- [ ] 上传接口不阻塞，PDF 解析由 Kafka Worker 完成。
- [ ] MinerU 解析结果包含 Markdown、图片和页码信息。
- [ ] 文档内容已写入向量库。
- [ ] 支持纯文本 RAG 问答。
- [ ] 支持图片 + 文字提问。
- [ ] 回答支持文字与 PDF 原图混排。
- [ ] 回答包含文档来源和页码。
- [ ] 解析失败时能看到错误信息。
- [ ] 重复消费不会产生重复数据。

## 16. 风险和注意事项

1. 上传接口不要同步调用 MinerU。
2. Kafka 是至少一次投递，Worker 必须做幂等处理。
3. MinerU 图片需要转换成前端可访问的 URL 或静态资源路径。
4. 模型只能引用检索结果中存在的图片 ID。
5. 不要将整本 PDF 全部发送给 LLM，只发送 Top-K 内容。
6. 每次最多向模型发送 3～5 张图片，控制上下文大小。
7. 长任务要有超时、重试和失败状态。
8. 状态以数据库为准，Kafka 消息用于驱动任务和通知变化。