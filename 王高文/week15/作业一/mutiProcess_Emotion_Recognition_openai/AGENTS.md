# 项目协作说明

本说明适用于整个工作区。项目当前处于 V1.0 目录骨架与设计阶段，功能范围、环境变量和完整验收清单见 [README.md](README.md)。

## 沟通与文档

- 默认使用简体中文沟通、编写项目文档和报告验证结果。
- 模型 ID、环境变量、API 字段、代码标识符和终端命令保留原名。
- 汇报改动时说明实际完成的内容、运行的验证和待验证事项。

## V1.0 技术约定

- 情绪识别与情感回应两个 Agent 均使用小米 MiMo 的 `mimo-v2.6-flash`，由 OpenAI Agents SDK 编排。
- 使用 `MIMO_API_KEY` 和 `MIMO_BASE_URL` 配置 `AsyncOpenAI`，通过 `OpenAIChatCompletionsModel` 调用 MiMo 的 OpenAI 兼容接口。
- 优先实测 Agent 1 的 Pydantic `output_type` 与真实 MiMo API 的兼容性。若 `json_schema` 请求不兼容，使用 `json_object` 并在后端执行 Pydantic 校验。
- FastAPI 负责会话管理和 Agent 调用，一次性返回完整回复。Streamlit 默认显示聊天，调试模式展示两个 Agent 的执行状态和本轮情绪识别结果。
- SQLite 保存聊天记录和 Agent 会话数据，路径分别由 `DATABASE_PATH` 和 `AGENT_SESSION_DB` 配置。示例配置使用占位密钥。

## 模块职责

| 计划路径 | 职责 |
| --- | --- |
| `frontend/` | 聊天输入、历史展示和调试开关 |
| `backend/main.py` | FastAPI 应用入口 |
| `backend/agents/` | 两个 Agent 的职责、提示词和模型配置 |
| `backend/services/` | 单轮聊天编排和会话管理 |
| `backend/models/` | Pydantic 数据结构 |
| `backend/database/` | SQLite 存储访问 |
| `tests/` | 功能、持久化和异常路径测试 |

## 开发与验证

按模型接入与实测、双 Agent 实现、FastAPI 编排、SQLite 持久化、Streamlit 界面、系统测试的顺序推进。验收时核对 README，并覆盖以下行为。

- 每轮重新识别当前消息，返回正面、负面或中性倾向及细分情绪。回应 Agent 结合识别结果和同一会话的历史生成回复。
- 使用原会话 ID 验证多轮聊天，并在重启后恢复已保存的消息。
- 分别验证 MiMo API 调用失败、结构化输出校验失败和高风险表达的处理。即使情绪分类失败，高风险处理流程也应可用。
- 检查 Streamlit 默认视图与调试视图，以及 FastAPI 的非流式响应。以实际运行结果为准，分别记录模拟测试和真实 MiMo 联调的状态。
