# Week15 作业 · 多进程情绪疏导系统（LangGraph 编排）

一个用 **LangGraph** 串联的「情感识别 → 分支鼓励/排解」对话应用。
情感识别、共情回复、后端编排、前端页面各跑在**独立进程**里，进程之间用 HTTP 通信。

---

## 1. 需求与目标

用户输入一段文字，系统：

1. **进程1 · 情感识别**：判断这段话是 `positive` 还是 `negative`；
2. **进程2 · 共情回复**：
   - 识别为正向 → **鼓励**（肯定、放大积极面）；
   - 识别为负面 → **排解**（共情、承接情绪、给可执行的小建议）；
3. **后端进程**：接收前端请求，用 LangGraph 把进程1、进程2 串成一条有分支的图；
4. **前端进程**：提供聊天页面，用户输入并展示识别结果与回复。

### 明确的非目标（本次不做）

| 不做 | 原因 |
| --- | --- |
| 多轮对话记忆 | 单轮「输入 → 识别 → 回复」已覆盖题目要求 |
| 流式输出 | 演示价值低，首字延迟不是本次考点 |
| 数据库/日志持久化 | 无历史查询需求 |
| 用户系统、鉴权 | 本地作业演示 |
| 细粒度情绪分类（愤怒/焦虑/悲伤…） | 题目只要求正/负二分 |

---

## 2. 架构

```
┌────────────────────┐   HTTP    ┌──────────────────────────┐
│  前端进程 :5001     │ ────────► │  后端进程 :5000           │
│  frontend_service  │           │  backend_service          │
│  Flask + HTML 聊天页│ ◄──────── │  LangGraph 编排           │
└────────────────────┘  JSON     └───────┬──────────┬────────┘
                                        │ HTTP      │ HTTP
                                 ┌──────▼─────┐ ┌───▼──────────┐
                                 │ 进程1 :5002 │ │ 进程2 :5003   │
                                 │ 情感识别    │ │ 共情回复      │
                                 └────────────┘ └───────────────┘
```

- 每个进程可以**单独启动、单独 curl 调试**，互不耦合。
- 后端只负责编排，不内嵌任何识别/生成逻辑，所以换模型或换规则只改单个服务。
- 四个进程由一个启动脚本统一拉起，Ctrl+C 一次全部退出。

### 端口约定

| 端口 | 进程 | 职责 |
| --- | --- | --- |
| 5001 | 前端 | 聊天页面 + 静态资源 |
| 5000 | 后端 | LangGraph 编排，对外唯一业务 API |
| 5002 | 进程1 | 情感识别 |
| 5003 | 进程2 | 鼓励 / 排解回复生成 |

---

## 3. LangGraph 设计

### 状态（State）

```python
{
    "message":   str,       # 用户原始输入
    "sentiment": {          # 进程1 返回
        "label":  "positive" | "negative",
        "score":  float,    # 0.0 ~ 1.0，对该标签的置信度
        "reason": str,      # 一句话依据
    },
    "mode":      "encourage" | "comfort",   # 由 label 映射
    "reply":     str,       # 进程2 返回的回复
    "trace":     list[str], # 节点执行轨迹，前端展示用
    "error":     str | None,
}
```

### 图结构

```
START → node_sentiment ──┬─(label=positive)→ node_encourage ─→ END
                         └─(label=negative)→ node_comfort   ─→ END
```

- `node_sentiment`：`POST http://127.0.0.1:5002/api/analyze`
- `node_encourage` / `node_comfort`：`POST http://127.0.0.1:5003/api/reply`，仅 `mode` 不同
- 条件边 `route_by_sentiment(state)` 读 `state["sentiment"]["label"]`
- 任一节点失败：捕获异常写入 `state["error"]` 并直接结束，后端返回 502，前端展示错误提示（不伪造回复）

### 为什么用 LangGraph 而不是 if-else

分支路由、节点状态、执行轨迹都由图管理；后续要加「危机干预节点」「多轮记忆节点」时只需加节点和边，编排代码不动。

---

## 4. 接口契约

### 4.1 进程1：情感识别

`POST /api/analyze`

```jsonc
// 请求
{ "text": "今天终于升职了" }

// 200 响应
{
  "label": "positive",
  "score": 0.93,
  "reason": "表达了升职带来的积极情绪",
  "elapsed": 0.82
}
```

- 二分类，禁止中性：模型必须选一个；**模糊/中性/混合情绪的文本一律判 `negative`**（保守共情，避免对低落用户漏排解）。
- 空 `text` → `400 {"error": "text 不能为空"}`
- 未配置 API Key → `500 {"error": "未配置 DS_API_KEY"}`

### 4.2 进程2：共情回复

`POST /api/reply`

```jsonc
// 请求
{ "text": "我被裁员了", "label": "negative", "reason": "表达失业的失落" }

// 200 响应
{ "mode": "comfort", "reply": "被裁员确实很难受……", "elapsed": 1.35 }
```

- `label` 只接受 `positive` / `negative`，其他值 → `400`
- 鼓励模式：先肯定具体的事，再给一句轻量行动建议；禁止说「这没什么」
- 排解模式：先共情命名情绪 → 再给 1～2 条可执行小建议；禁止空洞说教、禁止诊断式用语

### 4.3 后端：编排入口

`POST /api/chat`

```jsonc
// 请求
{ "message": "今天面试通过了" }

// 200 响应
{
  "message": "今天面试通过了",
  "sentiment": { "label": "positive", "score": 0.95, "reason": "..." },
  "mode": "encourage",
  "reply": "恭喜！……",
  "trace": ["node_sentiment", "node_encourage"],
  "elapsed": 2.1,
  "error": null
}
```

- 空 `message` → `400`
- 下游服务不可用/超时 → `502 {"error": "情感识别服务不可用: ..."}`

### 4.4 前端

- `GET /` → 聊天页
- 页面内 `fetch("/api/chat")` 打的就是前端自己的端口；前端服务把请求转发给 `:5000`，避免 CORS
- 四个服务都提供 `GET /health` → `{"status": "ok", "service": "..."}`，供启动脚本和自检探活

---

## 5. 目录结构与文件职责

```
week15/langgraph/          # 本项目根目录
├── README.md              # 本文档：需求、架构、接口、用例、验收
├── requirements.txt       # 依赖：flask、langgraph、langchain-openai、python-dotenv、requests、httpx
├── common.py              # 公共层：端口常量、向上查找并加载 root/.env、LLM 客户端、JSON 提取、长度裁剪
├── sentiment_service.py   # 进程1：情感识别 HTTP 服务（:5002），LLM 输出结构化 JSON
├── reply_service.py       # 进程2：鼓励/排解回复 HTTP 服务（:5003），两套 prompt 模板
├── backend_service.py     # 后端进程：LangGraph 定义（状态/节点/条件边）+ /api/chat + 下游错误归一
├── frontend_service.py    # 前端进程：Flask 托管 static/，把 /api/chat 原样转发给后端
├── run_all.py             # 启动脚本：端口占用检查 → 按序拉起 4 个进程并轮询 /health → Ctrl+C 全部回收
├── static/
│   ├── index.html         # 聊天页结构：输入框、发送、示例问题、气泡、识别标签、节点轨迹
│   ├── style.css          # 页面样式（正向/负向标签不同颜色、loading 动画）
│   └── app.js             # 前端逻辑：提交、渲染回复与轨迹、错误提示、loading、Enter 快捷键
└── tests/
    └── selfcheck.py       # 断言式自检脚本：自动跑第 7 节 14 条可判定用例
```

配置复用 `root/.env`（`DS_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`），不新增配置文件、不在代码里写死密钥。

---

## 6. 启动与调试

```powershell
cd 作业\week15\langgraph

# 1. 安装依赖（root 环境已装可跳过）
pip install -r requirements.txt

# 2. 一键启动四个进程（自动检查端口占用、等待各服务 /health 就绪）
python run_all.py

# 3. 打开页面
#    http://127.0.0.1:5001

# 4. 另开终端跑自检（需四个进程在运行）
python tests\selfcheck.py
```

单服务调试：

```powershell
python sentiment_service.py     # 只起进程1
curl -X POST http://127.0.0.1:5002/api/analyze -H "Content-Type: application/json" -d '{\"text\":\"今天很开心\"}'
```

---

## 7. 测试用例

> 用例在编码前冻结。`selfcheck.py` 自动跑 A/B/R/F 组中可判定的项；带「人工」标记的由截图或肉眼验收。

### 7.1 进程1 · 情感识别

| 编号 | 场景 | 输入 | 期望 |
| --- | --- | --- | --- |
| S-01 | 明显正向 | 今天终于升职了，太开心了 | `label=positive`，`score>=0.6` |
| S-02 | 明显负向 | 我被裁员了，真的很难受 | `label=negative`，`score>=0.6` |
| S-03 | 中性/模糊（边界） | 今天星期一 | `label=negative`（约定：模糊归负） |
| S-04 | 反讽（边界） | 太好了，又双叒加班到十二点 | `label=negative`（人工） |
| S-05 | 空文本 | `""` | HTTP 400，`error` 非空 |
| S-06 | 超长文本（边界） | 2000 字重复文本 | HTTP 200，不崩溃，不截断报错 |
| S-07 | 未配置 Key | 删除 `DS_API_KEY` 后启动 | HTTP 500，`error` 明确提示 |
| S-08 | 响应结构 | 任意合法输入 | 必含 `label/score/reason`，`score` 在 0~1 |

### 7.2 进程2 · 共情回复

| 编号 | 场景 | 输入 | 期望 |
| --- | --- | --- | --- |
| R-01 | 鼓励模式 | label=positive | `mode=encourage`；含肯定语；不出现「别难过」 |
| R-02 | 排解模式 | label=negative | `mode=comfort`；含共情 + 至少一条建议 |
| R-03 | 非法 label（边界） | label=neutral | HTTP 400 |
| R-04 | 空文本 | `""` | HTTP 400 |
| R-05 | 文本与 label 矛盾（边界） | 负面文本 + positive | 仍按传入 label 生成，不报错（人工：确认语气不自相矛盾） |
| R-06 | 回复长度 | 任意合法输入 | 60~300 字，单次调用 `elapsed` 可观测 |

### 7.3 后端 · LangGraph 编排

| 编号 | 场景 | 输入 | 期望 |
| --- | --- | --- | --- |
| B-01 | 正向端到端 | 我拿到 offer 了 | `mode=encourage`，`trace=["node_sentiment","node_encourage"]` |
| B-02 | 负向端到端 | 我和女朋友分手了 | `mode=comfort`，`trace=["node_sentiment","node_comfort"]` |
| B-03 | 情感服务宕机（边界） | 停掉 :5002 后请求 | HTTP 502，`error` 含「情感识别服务不可用」 |
| B-04 | 回复服务宕机（边界） | 停掉 :5003 后请求 | HTTP 502，`error` 含「回复服务不可用」 |
| B-05 | 下游超时（边界） | 服务端人为 sleep > 超时阈值 | HTTP 502，不无限阻塞 |
| B-06 | 空消息 | `{"message":""}` | HTTP 400 |
| B-07 | 非 JSON 请求体（边界） | 纯文本 body | HTTP 400，不 500 |
| B-08 | 并发 5 个请求 | 同时发送 | 全部 200，互不串数据 |

### 7.4 前端

| 编号 | 场景 | 操作 | 期望 |
| --- | --- | --- | --- |
| F-01 | 页面可达 | 访问 `:5001` | 200，显示输入框与发送按钮 |
| F-02 | 正向流程 | 输入正向文本发送 | 用户气泡 + `positive` 标签 + 鼓励回复 |
| F-03 | 负向流程 | 输入负向文本发送 | 用户气泡 + `negative` 标签 + 排解回复 |
| F-04 | 空输入（边界） | 不输入直接点发送 | 按钮禁用或提示「请输入内容」，不发请求 |
| F-05 | 请求中状态 | 发送后 | 出现 loading，期间不可重复提交 |
| F-06 | 后端异常 | 停掉 :5000 后发送 | 显示错误提示，页面不白屏 |
| F-07 | 超长输入（边界） | 粘贴 5000 字 | 前端提示或正常提交，不卡死 |

### 7.5 进程与集成

| 编号 | 场景 | 操作 | 期望 |
| --- | --- | --- | --- |
| I-01 | 一键启动 | `python run_all.py` | 4 个端口全部可访问 |
| I-02 | 优雅退出 | Ctrl+C | 4 个子进程全部退出，端口释放无残留 |
| I-03 | 单服务独立运行 | 只启动 `sentiment_service.py` | 服务正常，不依赖其他进程 |
| I-04 | 重复启动（边界） | 端口被占用时再次启动 | 明确报错提示端口占用，不静默失败 |

---

## 8. 验收标准

1. `python run_all.py` 一条命令拉起全部 4 个进程，页面可交互。
2. S-01/S-02、R-01/R-02、B-01/B-02 六个核心用例通过（截图存档）。
3. `tests/selfcheck.py` 全绿。
4. 停掉任一子服务后，用户看到的是**明确错误提示**，而不是假回复或白屏。
5. Ctrl+C 后无残留进程占用 5000~5003 端口。
