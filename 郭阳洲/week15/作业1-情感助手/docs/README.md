# 作业1 · 应用级情感助手（LangGraph vs OpenAI-Agents）

用 **LangGraph** 和 **OpenAI-Agents** 两套框架实现**完全相同的业务**：
带多轮对话与对话记忆的「情感陪伴助手」——检索记忆 → 识别情感（正向/负面/中性 + 强度）
→ 分支回应（鼓励/排解/中性）→ 回写记忆，支持多会话隔离与长期用户画像。

---

## 一、需求与角色划分

| 题目说法 | 实际含义 | 职责 |
|---------|---------|------|
| 进程1 | 情感识别节点/Agent | 输出 `positive/negative/neutral` + 情绪强度 |
| 进程2 | 回应生成节点/Agent | 正向鼓励、负面排解、中性平常回应 |
| 后端进程 | 编排串联层 | 记忆检索 → 识别 → 生成 → 记忆回写 四阶段闭环 |
| 前端进程 | Web 交互层 | 会话管理、对话、长期画像展示 |

功能清单：
- [x] 情感三分类 + 强度（结构化输出）
- [x] 分支回应：鼓励 / 排解 / 中性
- [x] 多轮对话，回应可引用历史（"还记得我刚才说的开心事吗"→ 答得出）
- [x] 短期记忆（会话内最近 10 条）+ 长期记忆（情绪趋势/关键事件/主题/近况摘要）
- [x] 多会话隔离（session_id），标题自动取首条输入前 20 字
- [x] Streamlit Web 前端，侧边栏切换两个后端对比
- [x] SQLite 持久化，重启不丢

## 二、目录结构（分层 package）

```
作业1-情感助手/
├── core/                        # ① 核心层
│   ├── config.py                #   .env 配置 + LLM 客户端工厂
│   ├── entities.py              #   pydantic 实体（全项目唯一实体源）
│   └── prompt_manager.py        #   Jinja2 提示词渲染（StrictUndefined）
├── prompts/                     # ② 提示词模板（五段式：角色/任务/约束/变量/输出格式）
│   ├── classify.j2              #   情感识别
│   ├── reply_cheer.j2           #   正向鼓励
│   ├── reply_comfort.j2         #   负面排解
│   ├── reply_neutral.j2         #   中性回应
│   └── memory_update.j2         #   长期记忆提炼
├── storage/                     # ③ 存储层
│   ├── database.py              #   SQLite 建库建表
│   └── repository.py            #   会话/消息/记忆 CRUD（出入皆 pydantic 实体）
├── flows/                       # ④ 编排层
│   ├── common.py                #   两框架共享：兜底解析、记忆渲染、合并去重
│   ├── langgraph_flow.py        #   LangGraph 六节点图 + 条件边
│   └── agents_flow.py           #   OpenAI-Agents 五 Agent + 显式编排
├── api/                         # ⑤ 接口层
│   ├── server_factory.py        #   FastAPI 工厂（两后端共用路由）
│   ├── langgraph_server.py      #   入口 :8001
│   └── agents_server.py         #   入口 :8002
├── frontend.py                  # ⑥ Streamlit 前端
├── requirements.txt
├── app.db                       # 运行时自动生成（SQLite）
└── docs/                        # 本文档 + 技术方案 + 对比总结
```

依赖方向：`api → flows → core + storage`，`storage → core.entities`，单向不回头。

## 三、运行步骤

> 全部命令在 `作业1-情感助手` 目录下执行；`.env` 在上一级 `openai-agents-sandbox`。

```powershell
pip install -r requirements.txt

# 终端 A：LangGraph 后端（:8001）
python -m api.langgraph_server

# 终端 B：OpenAI-Agents 后端（:8002）
python -m api.agents_server

# 终端 C：前端
streamlit run frontend.py
```

### 接口直测（可选）
```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8001/chat -Method Post `
  -ContentType "application/json" -Body '{"session_id":null,"text":"我今天升职了，好开心"}'
```
响应：
```json
{ "session_id": "…", "framework": "langgraph", "sentiment": "positive",
  "intensity": "high", "reply": "…", "memory_summary": "用户今天升职…" }
```

## 四、API 一览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/chat` | `{session_id?, text}` → 识别+回应+记忆更新（session_id 为空自动新建） |
| GET | `/sessions` | 会话列表 |
| GET | `/sessions/{id}` | 消息历史 + 长期画像 |
| DELETE | `/sessions/{id}` | 删除会话及全部记忆 |
| GET | `/health` | 健康检查 |

## 五、重要技术备忘（DeepSeek 思考模型兼容性）

实测 `deepseek-flash`（思考模型）的限制与本项目对策：

| 能力 | 状态 | 对策 |
|------|------|------|
| `response_format=json_schema` | ❌ 400 | LangGraph 改用 `with_structured_output(method="json_mode")` |
| `response_format=json_object` | ✅ | 即 json_mode，提示词需含 "JSON" 字样（模板已满足） |
| 强制 `tool_choice` | ❌ 400 | 不使用 |
| `tool_choice=auto` | ✅ | openai-agents 的 `output_type` 即此路径 |
| 纯文本 JSON | ✅ | 全链路最终兜底：`extract_json_dict`（去围栏→直解→正则） |

结构化输出策略：**框架原生结构化优先 → 文本 JSON 提取兜底 → 关键词兜底**，
长期记忆失败不阻塞主流程。

## 六、验证结果（真实 LLM 联调通过）

- 多轮记忆：第 2 轮问"还记得我刚才说的开心事吗" → "当然记得，你今天升职了" ✓
- 上下文串联：第 3 轮负面输入 → "升职的喜悦里混进一点焦虑，很自然…" ✓
- 长期画像：`key_events`（升职）、`topics`（工作）、`emotion_trend`、`last_summary` 全部写入 ✓
- 多会话隔离：新会话消息数独立 ✓
- 标题自动生成、会话删除 ✓
- 两框架行为一致，分支切换（负面→正向跨轮检测）正确 ✓
