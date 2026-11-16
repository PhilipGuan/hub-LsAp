# 交付清单（Delivery Checklist）

> 项目：基于 Kafka 的多模态 RAG 问答系统（homework2）
> 基线：`需求文档.md` v1.0；进度与验证记录见 `README.md` 当前进度章节

## 1. 交付物

| 类别 | 内容 |
| --- | --- |
| 源码 | `backend/`（FastAPI API 服务 + Kafka 解析 Worker）、`frontend/`（Vue3 + Element Plus）、`scripts/`（初始化/造数/一键启动） |
| 基础设施编排 | `docker-compose.yml`（Kafka KRaft / MySQL 8 / MinIO / Qdrant） |
| 配置模板 | `.env.example`（含全部配置项注释；实际 `.env` 不入库） |
| 文档 | `README.md`（快速开始/进度/选型）、`docs/设计说明.md`（架构与关键决策）、`docs/部署文档.md`（部署/配置/验收自检/FAQ）、`需求文档.md`（评审基线）、本清单 |
| 测试 | `backend/tests/` 31 个 pytest 用例（API 校验 / Worker 管道 / 状态机 / 问答 SSE / Prompt 组装） |
| 演示数据 | `scripts/make_test_pdfs.py` 生成含图测试 PDF（`data/test_pdfs/测试文档A/B.pdf`） |
| 运行数据 | `data/`（SQLite、原件、Markdown/图片产物、FAISS 索引、日志）——沙箱验证态，可整体清空重跑 |

## 2. 运行环境

### 2.1 硬件 / 系统

| 项 | 要求 |
| --- | --- |
| OS | Linux（示例在 Ubuntu 系容器验证）；macOS / Windows(WSL2) 亦可 |
| 内存 | ≥8G（MinerU CPU 模式加载模型约 4G） |
| GPU | 可选。无 GPU 用 MinerU CPU 模式（约 1~3 分钟/10 页）或有 GPU 显著提速 |
| 磁盘 | ≥20G（镜像 + MinerU 模型权重约 2~3G + 数据） |

### 2.2 软件依赖（沙箱验证版本）

| 组件 | 版本 | 说明 |
| --- | --- | --- |
| Python | 3.12 | venv：`backend/.venv` |
| Node.js | ≥18 | 前端 Vite 构建 |
| Kafka | 3.9.0（KRaft 单节点） | docker-compose 或本机已有 broker |
| MinerU | 4.1.7（`pip install "mineru[core]"`） | 主解析器，已安装并复验通过；需 `mineru server start` 本地引擎（`run_dev.sh` 自动拉起）；权重约 5G 从 ModelScope 下载 |
| PyMuPDF | 2.x | 降级解析器 + 测试造数 |
| torch | 2.15.0（CPU 可跑） | sentence-transformers / MinerU 依赖 |
| bge-small-zh-v1.5 | 512 维 | Embedding 模型，首次运行自动下载（走 `HF_ENDPOINT` 镜像） |

### 2.3 端口占用

| 端口 | 服务 |
| --- | --- |
| 5173 | 前端 Vite dev server |
| 8000 | 后端 API（`/api/health` 健康检查、`/docs` 接口文档） |
| 9092 | Kafka |
| 3306 / 9000 / 9001 / 6333 | MySQL / MinIO API / MinIO 控制台 / Qdrant（完整形态才需要） |

## 3. 账号与密钥

| 项 | 值 | 备注 |
| --- | --- | --- |
| 多模态 LLM | 智谱 API Key：已配置在 `.env` 的 `LLM_API_KEY`（`glm-5.3-flash`） | **敏感凭证：`.env` 不要提交到公开仓库**；交付时单独移交或由验收方自备 Key。模型必须为多模态（GLM-4.5V/GLM-5.3-Flash/Qwen-VL 等） |
| MySQL | root / root123（`docker-compose.yml`） | 作业演示用弱密码，生产需更换 |
| MinIO | minioadmin / minioadmin（`docker-compose.yml`） | 同上 |
| 系统登录 | 无（作业场景免鉴权，需求 §6 允许，已在设计说明注明） | 生产建议网关层加登录态 |

## 4. 启动步骤（验收机）

```bash
# 1) 基础设施（有 docker 时；无 docker 需本机已有 Kafka，如沙箱）
docker compose up -d

# 2) 后端依赖（首次）
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install "mineru[core]"

# 3) 配置与初始化
cp ../.env.example ../.env      # 填 LLM_API_KEY（沙箱 .env 已配好，跳过）
python ../scripts/init_kafka_topics.py
python ../scripts/init_db.py

# 4) 启动
bash ../scripts/run_dev.sh      # 一键：init + API(8000) + Worker + 前端(5173)
# 或分进程：uvicorn app.main:app --port 8000 / python -m worker.main / npm run dev

# 5) 自检
curl http://localhost:8000/api/health   # 期望 {"status":"ok","kafka_ready":true,...}
```

Worker 重启后首次解析约慢 30s（embedding 模型冷加载），属正常现象。

## 5. 演示脚本（约 10 分钟）

> 目标：按「上传 → 解析可视 → 状态兜底 → 问答 → 图问」顺序走完整链路，每步有可讲点的验证点。

1. **开场（1min）**：打开前端 `http://localhost:5173`。讲架构一句话：上传与解析经 Kafka 解耦，Worker 消费解析，问答走向量检索 + 多模态 LLM。
2. **批量上传（2min）**：在文档页一次拖入 `data/test_pdfs/测试文档A.pdf`、`测试文档B.pdf`（≥2 个文件，对应验收标准 1）。指出列表出现两条「待解析」，3 秒轮询后自动变「解析中（转圈）」→「解析完成」（MinerU CPU 解析约 1~2 分钟/文档，正好利用等待时间讲解 Kafka 异步削峰的设计意图），能看到页数与解析耗时。
3. **解析产物（1min）**：点「查看解析结果」，展示 Markdown 预览中还原的标题/文本/图片。
4. **异常路径（2min）**：上传一个损坏 PDF（验收机上备一个改后缀的 `.pdf` 文本文件），展示重试 3 次后状态变「解析失败」并显示原因；点「重新解析」演示 F2-5。
5. **多模态问答（3min）**：切到问答页。先文本提问「本系统用什么消息队列解耦上传与解析？」——展示流式打字效果、回答引用 [资料n]、来源列表含文档名页码。再提问「知识库里的示意图是什么颜色和形状？」——回答命中资料图片并以 Markdown 显示该图（验收标准 3）。
6. **图问（1min）**：点附图按钮上传任意一张本地图片（如随手截图），问「我上传的图片里是什么？」——LLM 结合图片内容回答，证明用户图片确实进入多模态 Prompt。
7. **收尾（可选）**：`/docs` 展示接口清单；`data/logs/` 展示含 task_id 的结构化日志；`data/vector_store/` 展示索引产物。

## 6. 验收流程（对应需求 §8 三条标准）

**前置**：服务已按 §4 启动且 `/api/health` 返回 `kafka_ready=true`；浏览器可打开前端。

### 验收 1：批量上传与异步解析（功能 1）

| 步骤 | 操作 | 通过标准 |
| --- | --- | --- |
| 1.1 | 前端一次上传 ≥2 个 PDF | 接口逐文件返回 `ok`，列表出现两条「待解析」 |
| 1.2 | 等待解析（Worker 日志可见消费过程） | 状态经「解析中」到「解析完成」 |
| 1.3 | 检查产物 | `data/markdown/<id>.md` 存在且含原文；`data/images/<id>/` 有图片；`data/vector_store/meta.json` 条目增加；解析失败场景见验收 2 |
| 1.4 | 重复上传同名/同内容文件（`.env` `ALLOW_DUPLICATE` 决定行为） | 默认允许重复入库；改 `false` 后提示「文件已存在」 |

### 验收 2：状态展示与异常（功能 2）

| 步骤 | 操作 | 通过标准 |
| --- | --- | --- |
| 2.1 | 观察 1.1 上传文件的列表状态 | 「待解析→解析中→解析完成」流转可见（3 秒轮询） |
| 2.2 | 上传损坏 PDF（文本文件改 `.pdf` 后缀） | 重试 3 次（约 20s）后变「解析失败」，显示失败原因 |
| 2.3 | 对失败文件点「重新解析」 | 状态回到「待解析」，再次走解析（仍损坏则再失败——预期行为） |
| 2.4 | （讲解项）Worker 消费为手动提交 offset | 停掉 Worker 再上传 → 消息积压；重启 Worker 后继续消费不丢失 |

### 验收 3：多模态问答（功能 3）

| 步骤 | 操作 | 通过标准 |
| --- | --- | --- |
| 3.1 | 文本提问「本系统用什么消息队列解耦上传与解析？」 | SSE 流式返回；回答基于知识库并附来源（文档名+页码） |
| 3.2 | 提问「知识库里的示意图是什么颜色？」 | 命中 `IMAGE` 块，回答含 `![...](/static/images/...)` 且图片正常显示 |
| 3.3 | 图问：附一张本地上传图片提问「我上传的图片是什么？和资料里的示意图一样吗？」 | 回答能描述**用户上传图片**的内容（证明图片进入 LLM）；来源列表正常 |
| 3.4 | （可选）检索范围选「选中文档」再提问 | 引用仅来自所选文档 |
| 3.5 | （日志核验，可选）`data/logs/` 或 debug 日志 | 可见召回条目与多模态消息组装记录 |

### 验收后收尾

- `backend && python -m pytest tests -v` → 31 passed
- 导出验收证据：前端关键界面截图、`/api/health` 响应、Worker 解析成功日志各一份留档

## 7. 已知边界（如实告知验收方）

1. 沙箱/开发形态使用 SQLite + 本地磁盘 + FAISS；完整形态切 MySQL/MinIO 仅需改 `.env`（代码已适配，但组合需按 §4 重新部署验证一次）。
2. MinerU 主解析器已在沙箱复验通过（4.1.7，basic 档 / CPU）：含图 PDF 75s 解析、图片块页码正确、问答可命中引用。CPU 解析速度约 30~90s/2页文档；验收机 ≥8G 内存可升级 standard 档（`mineru config set parse_server.local.managed_tier standard`）获得更好版面还原。PyMuPDF 降级解析器保留（`.env` 一键切换），无版面分析。
3. 问答多轮上下文保留最近 6 条消息；会话历史页为简化实现（列表 + 重开）。
4. 免鉴权（需求 §6 作业场景允许）；`/static` 图片地址可直接访问，生产需加权限层。
5. 思考模型（glm-5.3-flash）首 token 约 3~10s（reasoning 阶段），`LLM_THINKING_EFFORT=low` 已尽量压低；对「首 token <5s」指标在复杂提问时可能略超。
