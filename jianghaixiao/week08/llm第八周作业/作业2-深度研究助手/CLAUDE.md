# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## 项目概览

这是一个 FastAPI + Pydantic 的深度研究助手 MVP。输入研究主题，系统执行
关键词规划、网页搜索、内容总结、补检判断和报告生成。

核心约定：

- 默认必须可以在无 API Key 的 mock 模式下运行。
- 真实模型使用 OpenAI 兼容接口，真实搜索使用 Bocha HTTP API。
- 所有 Pydantic 模型集中在 `backend/models.py`。
- 业务状态只在 `backend/engine.py` 和研究任务层推进。
- 运行时数据保存在 `backend/data/research/`，不要手工修改。

## 常用命令

```powershell
python -m uvicorn backend.app:app --reload --port 8010
python -m pytest
python -m compileall backend
```

## 架构

```text
app.py
  -> research.py
    -> engine.py
      -> tools.py
      -> llm.py
      -> storage.py
```

`engine.py` 只负责确定性控制流；`llm.py` 负责模型调用并在无 Key 时降级为
mock；`storage.py` 负责 JSON 记录；`app.py` 只做 HTTP 参数校验和后台任务。

## 编码规范

- 注释和文档字符串使用中文。
- 新增字段必须同步更新 `models.py`。
- 不要在前端请求路径中直接拼接文件路径。
- 不要引入任务队列、数据库或复杂前端，除非需求明确要求。
- 每个新增功能至少保留一个手工验证命令或 pytest 用例。
