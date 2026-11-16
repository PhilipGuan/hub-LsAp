# 基于 Kafka 的多模态 RAG 问答系统

> 作业二 · 张利强 · week14

前端一次上传多个 PDF → Kafka 异步解耦 → 后端 MinerU 解析（Markdown + 图片）→ 分块向量化入 Elasticsearch → 支持图文混排与「图问」的多模态问答（智谱 GLM-4V）。

---

## 1. 功能

| 编号 | 功能 | 实现 |
| --- | --- | --- |
| F1 | 前端上传多个 PDF，经 Kafka 交给后端，MinerU 解析存储 | `POST /api/documents` → topic `pdf-parse` → `parse_worker` → MinerU 4.2 → Markdown + PNG 落盘 → ES 索引 |
| F2 | 展示解析状态：待解析 / 解析中 / 解析完成 | SQLite 状态机 + Kafka `doc-status` + SSE 实时推送，前端徽标自动变化 |
| F3 | 多模态问答 | ① 回答图文混排（引用文档原图，Markdown 渲染）② 用户可上传图片 + 文字一起送 GLM-4V |

状态机：

```text
pending(待解析) --Kafka 消费--> parsing(解析中) --成功--> completed(解析完成)
                                      |
                                      +--失败（重试 3 次）--> failed(解析失败) --重试--> pending
```

---

## 2. 架构

```text
┌──────────────┐   multipart   ┌──────────────────┐  produce   ┌──────────────┐
│ React (5173) │ ────────────> │ FastAPI (8000)   │ ─────────> │ Kafka 4.4    │
│ 上传/状态/问答│ <──────────── │ SQLite + 本地文件 │            │ pdf-parse    │
└──────┬───────┘  SSE 状态/流式 └────────┬─────────┘            │ doc-status   │
       │                                 │                      └──────┬───────┘
       │                                 │ 检索                        │ consume
       │                                 v                             v
       │                        ┌──────────────────┐        ┌──────────────────┐
       └──────────────────────> │ Elasticsearch    │ <───── │ parse_worker     │
                                │ BM25 + dense_vec │        │ MinerU + 分块    │
                                └──────────────────┘        │ + embedding      │
                                                            └──────────────────┘
```

- `pdf-parse`：3 分区，key = doc_id，消费组 `pdf-parse-workers`（可起多个 worker）
- `pdf-parse-dlq`：重试 3 次仍失败的死信
- `doc-status`：worker → API 的状态事件，API 消费后经 SSE 推给浏览器

---

## 3. 目录

```text
作业二/
├── backend/
│   ├── requirements.txt
│   └── app/
│       ├── main.py            # FastAPI 入口（lifespan 启动状态消费者）
│       ├── config.py          # .env 配置
│       ├── db.py / models.py  # SQLite：documents / sessions / messages
│       ├── kafka_client.py    # 生产者 + 消费者工厂
│       ├── status_consumer.py # doc-status → 进程内事件总线
│       ├── events.py          # SSE 事件总线
│       ├── api/               # documents / chat / sessions / events
│       ├── services/          # mineru_parser / chunker / embedder /
│       │                      # es_store / pipeline / llm
│       └── workers/parse_worker.py
├── frontend/                  # Vite + React + TS
├── scripts/                   # init_topics / start_all / stop_all / 应用启动脚本
├── storage/                   # 运行期数据（自动创建）
├── .env / .env.example
├── 需求澄清.md
└── README.md
```

---

## 4. 环境（本机实测）

| 组件 | 版本 / 位置 |
| --- | --- |
| macOS | arm64 |
| Kafka | 4.4.0（Homebrew，KRaft 单节点，:9092） |
| Elasticsearch | 9.5.2（本机已有实例，`127.0.0.1:9200`，单节点；索引副本数 0） |
| Python | conda `py312`（3.12.14）；项目 venv `.venv` |
| MinerU | 4.2.0，`/opt/anaconda3/envs/py312/bin/mineru` |
| Node | 23.x / npm 10.x |
| 模型 | 智谱 `GLM-4.6V-Flash`（主，免费）+ `glm-4v-flash`（限流兜底）+ `embedding-3`（1024 维） |

Python 依赖已在 `.venv` 中（基于 py312 的 `--system-site-packages`，复用 MinerU / torch / fastapi）。

> **Kafka 与 Elasticsearch 均为本机服务**：项目内不包含安装包、不负责启停，启动前请确保 `9200` / `9092` 可访问。
> 单节点 ES 已关闭磁盘水位检查：`cluster.routing.allocation.disk.threshold_enabled=false`（本机磁盘使用率接近 100%，否则新索引分片无法分配）。

---

## 5. 快速开始

### 5.1 启动全部服务

```bash
cd 张利强/week14/作业二
cp .env.example .env      # 首次：填入 ZHIPU_API_KEY
./scripts/start_all.sh
```

脚本会依次：

1. 检查本机 Elasticsearch（9200）与 Kafka（9092）连通性，不可用则直接报错退出
2. 创建/确认应用所需的 3 个 topic（`scripts/init_topics.sh`）
3. 确保本机 MinerU 服务运行
4. 启动 FastAPI（8000）与 parse worker
5. 若 `frontend/node_modules` 存在，启动 Vite（5173）

打开 <http://127.0.0.1:5173>，接口文档 <http://127.0.0.1:8000/docs>。

### 5.2 首次安装依赖

```bash
# 后端（使用 conda py312 创建 venv 并复用其依赖）
/opt/anaconda3/envs/py312/bin/python -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r backend/requirements.txt

# 前端
cd frontend && npm install && cd ..
```

### 5.3 单独启动

```bash
./scripts/init_topics.sh     # 仅创建 topic（Kafka/ES 需已在本机运行）
./scripts/start_backend.sh   # API :8000
./scripts/start_worker.sh    # Kafka 消费者
./scripts/start_frontend.sh  # 前端 :5173
```

停止：

```bash
./scripts/stop_all.sh        # 只停本项目的 API / worker / 前端
```

---

## 6. 配置（.env）

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `ZHIPU_API_KEY` | 智谱 Key | 空 |
| `LLM_MODEL` | 多模态模型 | `glm-4v-flash` |
| `LLM_FALLBACK_MODEL` | 主模型 429 限流时的兜底模型（空=不兜底） | 空 |
| `EMBED_MODEL` / `EMBED_DIM` | 向量模型 / 维度 | `embedding-3` / 1024 |
| `KAFKA_BOOTSTRAP` | Kafka 地址 | `127.0.0.1:9092` |
| `ES_URL` / `ES_INDEX` | ES 地址 / 索引名 | `http://127.0.0.1:9200` / `rag_chunks_v1` |
| `MINERU_BIN` / `MINERU_TIER` | MinerU 可执行文件 / 档位 | py312 路径 / `flash` |
| `MAX_UPLOAD_MB` | 单文件大小上限 | 50 |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | 分块参数 | 800 / 100 |
| `TOP_K` | 检索片段数 | 6 |
| `MOCK_LLM` / `MOCK_EMBEDDING` | 离线模拟开关 | `false` |

> 无 Key 或断网时，把 `MOCK_LLM` / `MOCK_EMBEDDING` 设为 `true`：embedding 用字符 n-gram 哈希向量，LLM 返回带图片引用的模拟回答，链路仍完整可演示。当前 `.env` 为 `false`（真实调用）。
> 免费多模态模型（如 `GLM-4.6V-Flash`）高峰可能返回 `429 / 1305 该模型当前访问量过大`；`llm.py` 会重试一次，再自动切到 `LLM_FALLBACK_MODEL`。

---

## 7. 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/documents` | 多文件上传（multipart `files`），sha256 去重 |
| GET | `/api/documents` | 列表；`?status_filter=completed` |
| GET | `/api/documents/{id}` | 详情 |
| POST | `/api/documents/{id}/reparse` | 重置状态并重新投递 Kafka |
| DELETE | `/api/documents/{id}` | 删除 DB + ES 块 + 文件 |
| GET | `/api/events` | SSE：`doc_status` 状态事件 + 心跳 |
| GET/POST | `/api/sessions` | 会话列表 / 新建 |
| GET | `/api/sessions/{id}/messages` | 历史消息 |
| POST | `/api/chat` | 问答（multipart：`text`、`images[]`、`session_id`），SSE 流 |
| GET | `/static/*` | PDF / 解析图片 / 聊天图片 |
| GET | `/api/health` / `/api/stats` | 健康检查 / 文档状态统计 |

问答 SSE 事件顺序：`session` → `sources` → 多个 `token` → `done`（出错为 `error`）。

---

## 8. 关键实现说明

### 8.1 MinerU 解析

```bash
mineru parse <pdf> -o storage/parsed/<doc_id>/doc.md --tier flash --pages all --force
```

产物 Markdown 中的图片是文档库定位符：

```markdown
![Image block](doc:450b881/tier:flash/page:1/block:8)
```

解析器正则扫描后逐张导出并改写：

```bash
mineru read "doc:450b881/tier:flash/page:1/block:8" --format image \
  -o storage/parsed/<doc_id>/images/page1_block8.png
```

最终 Markdown 中变成 `/static/parsed/<doc_id>/images/page1_block8.png`，回答引用时可被浏览器直接加载。

> `basic/standard` 档需要本地 parse-server（默认 `mode=disabled`），本项目使用实测可用的 `flash` 档；解析服务未启动时 worker 会尝试自动 `mineru server start`。

### 8.2 分块与检索

- 按 `<!-- page N -->` 标记确定页码，按标题切段，单块 ≤800 字、重叠 100 字。
- 每块写入 ES：内容（`cjk` 分词，免 IK 插件）、页码、标题、图片列表、1024 维向量。
- 检索：BM25 top20 + kNN top20 → RRF（k=60）融合 → top6。

### 8.3 多模态问答

- 用户图片存 `storage/chat/`，以 base64 随消息送 GLM-4V。
- 检索片段与「可用图片清单」写入 system prompt，并规定只能引用清单内路径。
- 输出转 Markdown 渲染；`sanitize_images()` 白名单校验（仅 `/static/parsed|chat/...`），防止模型编造外链。
- 模型调用带容错：主模型抛异常（免费模型高峰常见 `429 / 1305`）时重试一次，仍失败则切换到 `LLM_FALLBACK_MODEL`，都不可用才把错误作为 `token` 返回给前端。

---

## 9. 演示流程

1. `./scripts/start_all.sh`
2. 打开 5173，拖入 1~2 个 PDF（建议 10 页以内）
3. 观察徽标：待解析 → 解析中 → 解析完成（SSE 实时更新，无需刷新）
4. 提问：「这个文档讲了什么？把里面的图也展示出来」
5. 再点 🖼 上传一张图片 + 「这张图和文档里的内容有关系吗？」

---

## 10. 常见问题

| 现象 | 处理 |
| --- | --- |
| 401/429 `余额不足或无可用资源包` | 智谱账号充值，或 `.env` 开 `MOCK_LLM/MOCK_EMBEDDING=true` |
| 429 `1305 该模型当前访问量过大` | 免费模型高峰限流：已自动重试 + 切 `LLM_FALLBACK_MODEL`；仍失败可换 `glm-4v-flash` / `glm-4v-plus` |
| 索引 red / `no_shard_available_action_exception` | 磁盘超过水位线导致分片无法分配；已关闭 `cluster.routing.allocation.disk.threshold_enabled`，并清理磁盘 |
| 文档一直「待解析」 | 确认 worker 在跑：`tail -f .runtime/logs/worker.log` |
| 解析失败提示 MinerU 服务未运行 | `mineru server start`（脚本已自动尝试） |
| ES 连接失败 | 启动本机 Elasticsearch；`curl localhost:9200` |
| `Topic doc-status not found` | 运行 `./scripts/init_topics.sh` |
| 端口占用 | `lsof -i :8000` / `:5173` / `:9200` / `:9092` |
| 磁盘紧张 | 本机剩余空间较小，ES / MinerU 模型较占空间，建议演示用小 PDF |
| SSE 不更新 | 确认只跑了一个 API 进程（事件总线是进程内的），或等待 8s 轮询兜底 |

---

## 11. 真实模型运行记录（实测）

`ZHIPU_API_KEY` 就绪后，`.env` 切为 `MOCK_LLM=false` / `MOCK_EMBEDDING=false`，实测结果：

| 验证项 | 结果 |
| --- | --- |
| `embedding-3` 真实调用 | ✅ 返回 1024 维向量 |
| 文档重新解析 | ✅ `test.pdf` → 2 chunks，ES 计数 2，索引 `green` |
| 文字问答流式输出 | ✅ `session → sources → 291 × token → done` |
| 回答引用文档图片 | ✅ 输出 `/static/parsed/<doc_id>/images/page1_block8.png` |
| 图问（文字 + 图片） | ✅ 176 × token，模型正确描述上传图片 |
| 静态资源 | ✅ 解析图 HTTP 200 / 13344 bytes |
| 前端代理 `/api` | ✅ 正常返回文档列表 |
| `tsc --noEmit` / 后端导入 | ✅ 通过 |

**从 mock 切到真实模型的注意事项**

1. mock 模式下入库的是字符 n-gram 哈希向量，与真实 embedding 不在同一空间。切换后必须对已有文档执行 `POST /api/documents/{id}/reparse`（或删除重传），否则向量检索结果无意义。
2. `reparse` 无需重新上传 PDF：原始文件保留在 `storage/uploads/<doc_id>.pdf`。
3. 免费多模态模型（`GLM-4.6V-Flash`）高峰期多次出现 `429 / 1305`，因此默认配置了 `LLM_FALLBACK_MODEL=glm-4v-flash` 作为兜底。
