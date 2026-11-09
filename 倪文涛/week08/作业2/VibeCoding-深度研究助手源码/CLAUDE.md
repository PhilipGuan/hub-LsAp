# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目状态与语言

「深度研究助手」MVP(八斗AI 综合案例 02)。**代码已生成**(编排器 + 五智能体 + 前端单页,结构见下),待填入 DeepSeek key 后做真实主题验收。README.md 是完整设计文档,所有产品决策已与用户对齐并冻结在共识 v2 中,不要擅自更改决策表里的结论。

与用户交流、代码注释、UI 文案均使用中文;报告输出语言跟随用户输入的研究主题。

## 常用命令

```bash
# 后端:conda 环境 badou_env3.12(Python 3.12),命令在项目根目录执行
conda run -n badou_env3.12 pip install -r backend/requirements.txt   # 依赖:fastapi uvicorn httpx beautifulsoup4 markdown,保持最少化
conda run -n badou_env3.12 python backend/main.py                     # http://127.0.0.1:7080(自带 reload)

# 前端:Node v22.14.0(nvm,frontend/.nvmrc 已固定)
cd frontend && nvm use && npm install && npm run dev    # 开发模式 http://127.0.0.1:6174,/api 代理到 7080
cd frontend && npm run build                            # 构建产物 dist/ 由后端伺服(7080)
```

无测试框架、无构建链、无 lint 配置(刻意保持极简,勿引入)。

## 架构(实现时必须遵循)

编排器 + 五智能体,公共能力全部沉淀在 `BaseAgent`。前后端分目录:`backend/`(FastAPI + 智能体)与 `frontend/`(Vue 3 + Vite 单页)。编排逻辑用普通 Python 确定性循环(**不**交给 LLM 决策):

- `backend/main.py` — 程序入口,`python backend/main.py` 启动(内部 `uvicorn.run("app:app")`,自带 reload)
- `backend/app.py` — FastAPI 应用 + 路由(首页、`/api/research` SSE 事件流),首页/静态目录指向 `frontend/`
- `backend/orchestrator.py` — 研究循环编排器(确定性循环:规划 → 检索 → 阅读 → 补检判断 → 综合)
- `backend/config.py` — `.env` 配置加载(密钥 + 循环参数)→ `CONFIG` 单例
- `backend/agents/base.py` — `BaseAgent` 抽象基类:LLM 调用封装(重试/超时)、SSE 事件上报、结构化输出解析(JSON 容错)、运行日志
- `backend/agents/planner.py` — 主题拆 4 个子问题
- `backend/agents/searcher.py` — 生成/优化查询词,调博查 Web Search
- `backend/agents/reader.py` — 抓网页正文抽取要点;每次搜索只取前 3 条 URL,失败降级用搜索 summary(需在过程流注明)
- `backend/agents/reflector.py` — 每轮末评估信息缺口:生成下轮补充查询,或判「信息充分」提前收敛
- `backend/agents/writer.py` — 带行内 `[n]` 引用综合生成 Markdown,转自包含 HTML(页内展示 + 下载独立 `.html`)
- `frontend/` — Vue 3 + Vite 单页(Node v22.14.0,`.nvmrc` 已固定):`src/App.vue` 承载输入框、SSE 过程流、报告 iframe 与下载;开发模式 Vite(6174)把 `/api` 代理到 7080,`npm run build` 后 `dist/` 由后端伺服

研究循环规则:第 1 轮每子问题各搜 1 次;第 2、3 轮按缺口补检(每轮 ≤4 次);**3 轮为硬上限,ReflectorAgent 可提前收敛**;单轮内搜索与抓页并发。

## 硬性约束

- **密钥与参数只放 `.env`**(博查 key、DeepSeek 三件套、9 个循环参数),代码通过环境变量读取;README 只留占位符,勿把 key 写回任何代码或文档
- LLM 走 OpenAI 兼容接口,`.env` 三件套(`LLM_BASE_URL/LLM_API_KEY/LLM_MODEL`)可切换,代码中不写死模型名
- 单点失败(搜索/抓页/LLM)统一策略:重试 1 次后跳过并在 SSE 过程流注明,任何单点故障不得中断整体流程
- 无数据库、无鉴权、无历史记录(单次会话)——这些是决策,不是欠账,勿"顺手补上"(前端构建链为 Vite,属既定决策)
- 报告无来源的结论必须标「模型推断」;引用编号必须与末尾来源列表一一对应

## 范围纪律

当前只做 MVP,验收标准 4 条见 README(真实主题跑通 ≤3 轮 / 报告四结构 / 引用可对 / 单点失败不崩)。Backlog(过程记录面板、置信度标注、部署)在 MVP 验收通过且用户明确提出前**不要实现**。
