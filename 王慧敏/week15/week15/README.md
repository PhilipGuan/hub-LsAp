# 作业1 & 作业2：情感识别与回应服务

分别使用 **LangGraph** 和 **openai-agents** 实现同一套情感识别 → 回应逻辑，并用 FastAPI + 前端页面串起来；作业2 基于 openai-agents 版本编写 Dockerfile 打包镜像。

## 作业1 需求 → 实现映射

| 需求 | 实现 |
| --- | --- |
| 进程1：接受请求进行情感识别 | `app/sentiment.py`（共享）+ `langgraph_impl.py` 的 `analyze_sentiment` 节点 / `openai_agents_impl.py` 的 `sentiment_agent` |
| 进程2：正向鼓励、负面排解 | LangGraph 的 `encourage`/`comfort` 节点；openai-agents 的 `encourage_agent`/`comfort_agent` |
| 后端进程：接受前端输入，串联进程1+2 | `app/main.py`（FastAPI，`POST /api/analyze`） |
| 前端进程：用户输入 | `frontend/index.html`（原生 HTML/JS） |

## 目录结构

```
.
├── app/
│   ├── config.py              # 环境变量、模型配置
│   ├── sentiment.py           # 进程1 共享逻辑（LLM + 离线规则兜底）、模板回复
│   ├── langgraph_impl.py      # LangGraph 版（图编排）
│   ├── openai_agents_impl.py  # openai-agents 版（Agent 编排）
│   └── main.py                # 后端进程（FastAPI）
├── frontend/index.html        # 前端进程
├── demo.py                    # 命令行自测（不启动 Web）
├── requirements.txt
├── Dockerfile                 # 作业2
└── .env.example
```

## 设计要点

- **进程1（情感识别）**：配置了 `OPENAI_API_KEY` 时调用 LLM；未配置时退化为关键词规则判断，保证离线也能跑通。
- **进程2（正向鼓励/负面排解）**：
  - LangGraph 用「条件边」按情感把流程路由到 `encourage` / `comfort` 两个节点；
  - openai-agents 用「结构化输出 + 显式路由」在 `encourage_agent` / `comfort_agent` 中二选一（等价写法 `handoff()` 见源码注释）。
- 两个实现暴露**同名入口 `run(text) -> dict`**，后端据此无差别串联。

## 快速开始

```bash
# 1. 创建虚拟环境并安装依赖（建议 Python 3.10/3.11）
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt

# 2.（可选）配置 LLM
copy .env.example .env     # Windows；Linux/macOS: cp .env.example .env
# 编辑 .env 填入 OPENAI_API_KEY（不填则离线规则兜底，示例仍可运行）

# 3a. 命令行自测（无需 Web）
python demo.py

# 3b. 启动后端 + 前端
uvicorn app.main:app --host 0.0.0.0 --port 8000
# 浏览器打开 http://localhost:8000
```

### 接口示例

```bash
# 使用 LangGraph 实现
curl -X POST http://localhost:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"text": "今天面试通过了，太开心啦！", "impl": "langgraph"}'

# 使用 openai-agents 实现
curl -X POST http://localhost:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"text": "最近压力好大，感觉很沮丧。", "impl": "openai-agents"}'
```

返回示例：

```json
{
  "sentiment": "positive",
  "response": "太棒了！…继续保持这份好心情…",
  "engine": "langgraph",
  "impl": "langgraph"
}
```

## 作业2：Docker 打包镜像

基于作业1 的 **openai-agents** 版本（`DEFAULT_IMPL=openai-agents`）。

```bash
# 1. 本地构建
docker build -t sentiment-agent:latest .

# 2. 本地运行
docker run -d -p 8000:8000 \
  -e OPENAI_API_KEY=your-key \
  -e OPENAI_MODEL=gpt-4o-mini \
  --name sentiment-agent \
  sentiment-agent:latest

# 浏览器打开 http://localhost:8000
```

### 推送到阿里云镜像服务（ACR）

```bash
# 1. 登录阿里云容器镜像服务
docker login --username=<你的阿里云账号> registry.cn-hangzhou.aliyuncs.com

# 2. 打标签（替换 <namespace> 为你的命名空间）
docker tag sentiment-agent:latest \
  registry.cn-hangzhou.aliyuncs.com/<namespace>/sentiment-agent:latest

# 3. 推送
docker push registry.cn-hangzhou.aliyuncs.com/<namespace>/sentiment-agent:latest
```

> 也可通过「阿里云容器镜像服务控制台 → 创建镜像仓库 → 上传镜像」走图形化流程，`registry.cn-<地域>.aliyuncs.com` 以控制台显示的地址为准。

## 说明

- 未配置 `OPENAI_API_KEY` 时，两个实现都用内置规则/模板兜底，流程照常跑通，便于无 Key 演示。
- 使用 OpenAI 兼容服务（如阿里云 DashScope）时，在 `.env` 设置 `OPENAI_BASE_URL` 与 `OPENAI_MODEL` 即可。

## 常见问题（Windows / conda）

1. **`FileNotFoundError: ... SSL_CERT_FILE`**：conda 环境可能把 `SSL_CERT_FILE` 指向一个不存在的 `ssl/cacert.pem`。已在 `app/config.py` 做了导入期自修复（自动改指 `certifi`）；若仍遇到，可手动执行：
   ```bash
   export SSL_CERT_FILE="$(python -c 'import certifi; print(certifi.where())')"
   ```

2. **命令行打印中文乱码**：接口返回的是标准 UTF-8，浏览器前端显示正常；仅 Windows 控制台 `python demo.py` 输出可能乱码。可加环境变量 `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8` 解决。

3. **用 curl 提交中文 JSON 报 `error parsing the body`**：Windows 下 curl 会把中文参数按本地编码（GBK）发送导致。建议用浏览器前端，或先把 JSON 写入 UTF-8 文件再 `curl --data-binary @file.json`。
