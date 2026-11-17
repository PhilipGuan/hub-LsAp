# parse-worker（解析工作器）

负责把上传的 PDF 变成可检索的数据：**MinerU 解析 → 文本切片 + 图片资源 → 向量入库 → 更新文档状态**。

## 运行

```bash
# 1) 先起后端（建表、提供对象存储与数据库）
uvicorn backend.app.main:create_app --factory --port 8000

# 2) 常驻处理待解析文档（默认本地 PyMuPDF 文本解析 + 假向量，不依赖 MinerU / 模型接口）
python parse-worker/worker.py --serve

# 3) 调真实 MinerU 服务 + 真实向量模型（密钥读 deploy/.env 的 EMBEDDING_*）
MINERU_API_URL=http://127.0.0.1:8080 python parse-worker/worker.py --serve
```

自测（不依赖任何外部服务，各自带 main）：

```bash
python parse-worker/mineru_runner.py   # MinerU 调用与 mock
python parse-worker/chunker.py         # Markdown 切片与图文关联
python parse-worker/indexer.py         # 幂等入库 + 向量
python parse-worker/worker.py          # 端到端：成功链路、幂等、自动重试耗尽
```

工作器取任务的方式：本地按**数据库状态机**轮询 `documents.status ∈ {queued, retrying}`（README 第一版允许的最小实现）；
Kafka 阶段换成消费 `parse.requested` 即可，`indexer` 的“先清理再写入”保证重复消息不产生重复切片。

## 文件

| 文件 | 作用 |
| ---- | ---- |
| `mineru_runner.py` | MinerU HTTP 客户端；无 `MINERU_API_URL` 时用 `MockMineruParser` 生成确定性 Markdown/图片 |
| `chunker.py` | 按 `<!-- page: N -->` 切页、段落合并成切片、同页图片绑定到切片 |
| `indexer.py` | 图片写对象存储、切片/图片落库、向量生成；先删后写（幂等） |
| `worker.py` | 轮询取任务、状态机流转（mark_parsing → mark_indexing → mark_done）、失败转重试/死信 |

## 环境变量

| 变量 | 默认 | 说明 |
| ---- | ---- | ---- |
| `MINERU_API_URL` | 空 | MinerU HTTP 服务地址；为空时用本机 PyMuPDF 解析（真实页数/文字/内嵌图片），未装 PyMuPDF 才退回 mock |
| `MINERU_MOCK` | 自动 | `1` 强制 mock、`0` 强制真实服务 |
| `MINERU_TIMEOUT` | `300` | MinerU 调用超时（秒） |
| `WORKER_POLL_INTERVAL` | `1.0` | 空闲时轮询间隔（秒） |
| `WORKER_BATCH_SIZE` | `4` | 单轮最多处理文档数 |
| `WORKER_STUB_MODELS` | `0` | `1` 用确定性假向量（与智能体 `AGENT_STUB_LLM=1` 的假向量维度一致） |

## 状态流转

```text
queued/retrying -> parsing -> indexing -> done
                      |           |
                      v           v
                 失败 -> 自动重试（最多 3 次）-> failed -> 死信事件
```

状态写回数据库后，前端通过 SSE + 5 秒轮询看到变化（工作器进程不持有前端 SSE 连接）。
