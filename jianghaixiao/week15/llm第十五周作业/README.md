# 第十五周作业

本目录完成两个作业：

1. 分别使用 LangGraph 和 OpenAI Agents 实现“情感识别 -> 情绪回复”的串联流程。
2. 为 OpenAI Agents 版本编写 Dockerfile，并说明镜像构建和运行方式。

## 目录结构

```text
第十五周作业/
├─ README.md
├─ langgraph/
│  ├─ app.py
│  ├─ requirements.txt
│  ├─ .env.example
│  └─ static/index.html
└─ openai_agents/
   ├─ app.py
   ├─ requirements.txt
   ├─ Dockerfile
   ├─ .dockerignore
   ├─ .env.example
   └─ static/index.html
```

## 业务流程

```text
浏览器输入
   |
   v
FastAPI 后端
   |
   +--> 进程 1：情感识别 Agent
   |      positive / negative / neutral
   |
   +--> 进程 2：回复 Agent
          正向 -> 鼓励
          负面 -> 共情与排解
          中性 -> 正常交流
   |
   v
浏览器展示情感、判断理由和回复
```

这里的“进程 1/2”对应两个独立的 Agent 阶段，由后端负责串联。

## 运行 LangGraph 版本

```powershell
cd .\langgraph
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# 编辑 .env，填写 OPENAI_API_KEY
uvicorn app:app --reload --port 8001
```

访问 `http://127.0.0.1:8001`。

## 运行 OpenAI Agents 版本

```powershell
cd .\openai_agents
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# 编辑 .env，填写 OPENAI_API_KEY
uvicorn app:app --reload --port 8000
```

访问 `http://127.0.0.1:8000`。

## 作业 2：Dockerfile

作业 2 的 `Dockerfile` 位于 `openai_agents/Dockerfile`。

Dockerfile 是构建 Docker 镜像的文本说明文件：

```text
Dockerfile --docker build--> 镜像 --docker run--> 容器
```

构建镜像：

```powershell
cd .\openai_agents
docker build -t emotion-agent:1.0 .
```

运行容器：

```powershell
docker run --rm -p 8000:8000 --env-file .env emotion-agent:1.0
```

访问 `http://127.0.0.1:8000`。

API Key 在容器运行时通过 `--env-file` 注入，不会被打包进镜像。