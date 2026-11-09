# 研迹 · 深度研究助手

第八周作业二：综合案例 02。以课程中“输入主题，输出结构化研究报告、来源、过程和置信度”的需求为规格，通过自然语言需求 → AI 编码 → 测试反馈 → 修正实现。

## 快速运行

需要 Python 3.10+。**应用不需要安装第三方包**。在当前目录运行：

```powershell
python app.py --port 8008
```

然后访问 http://127.0.0.1:8008/ 。也可双击 `start.bat`。
选择“离线演示”，点击“开始研究”，会用固定教学样例跑完两轮流程。不是预置完成页面；每次点击都会新建任务并经过真实控制流。

## 实时联网研究

将 `.env.example` 复制为 `.env`，配置你自己的 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`、`BOCHA_API_KEY`，重启服务并选择“实时联网”。

真实模式接入 Bocha 搜索和 OpenAI 兼容 Chat Completions 模型 API。项目不会读取课程压缩包中的密钥，也没有嵌入密钥。实时模式会将主题与搜索资料发送给你配置的服务。

已完成离线流程与本地界面端到端验证；未使用真实 Bocha 密钥执行完整联网研究。真实服务的可用性、额度与模型 JSON 输出仍需在你配置后验证。

## 实现结构

```text
deep-research/
  app.py                 HTTP 路由、请求校验、任务队列
  engine.py              规划 → 搜索 → 抽取 → 判断补检 → 报告
  providers.py           固定演示数据 / 真实 LLM 与 Bocha
  storage.py             原子 JSON 持久化、重启恢复
  prompts/               四个角色的模型提示词
  dist/                  HTML、CSS、JavaScript 前端
  tests/                 单元与研究循环测试
  .claude/skills/         research-check 项目技能
  .claude/hooks/          自动审计事件 Hook
  .claude/settings.json   Hook 和 MCP 配置
  .mcp.json              本地 MCP 连接
  tools/                 MCP 服务、Claude 验证与截图脚本
  evidence/              实际日志、截图、样例报告和测试记录
```

不复制课程参考后端，重新实现为标准库版本，方便 Windows 直接运行。应用不依赖 OpenAI Agents SDK；四个角色由独立提示词驱动，确定性引擎编排其调用。

## 核心功能

- 2–4 轮上限，补检判断支持提前收敛、新关键词补检和无新关键词停止。
- 每一步保存时间、轮数、关键词、来源和阶段结果；失败保留已有资料。
- URL 去重、来源编号校验；非法引用整条降为“模型推断”。
- 摘要、分节、关键结论、遗留问题、来源列表、置信度与时效说明。
- 网页历史记录、任务状态轮询、引用跳转、Markdown / JSON 下载、手机布局。
- 有界任务队列、HTTP 输入校验、跨来源写入限制、HTML 转义。

研究读取的是**搜索 API 摘要**，不宣称访问过完整网页。引用编号可追溯不等于事实正确；实时报告标为待人工复核。离线演示固定为“RAG 与微调如何选择”，不接受其他主题以免产生貌似真实的结果。

## 验证

```powershell
python -m unittest discover -s tests -v
node --check dist/app.js
```

`evidence/test-results.txt` 为测试结果，`evidence/browser-qa.json` 为浏览器验收结果。
浏览器截图脚本为可选开发工具，需要 Node.js、Playwright 和 Edge；可 `npm install --no-save playwright` 后运行 `node tools/browser-qa.cjs`，或通过 `PLAYWRIGHT_MODULE` 指定现有 Playwright 模块目录。运行前先启动网页服务。

## Claude Code 作业一

本机验证环境为 Claude Code 2.1.162，沿用已配置的 DeepSeek Anthropic 兼容服务；并不表示调用了 Anthropic Claude 模型。

```powershell
# 在本项目目录运行
claude mcp list
claude
```

在 Claude Code 中输入 `/research-check` 可执行只读项目验收；要求“调用 course_checklist，week=8”即可使用 MCP。SessionStart / PostToolUse 会自动写入 `evidence/hook-events.jsonl`。
也可以运行 `tools/verify-claude.ps1` 一次验证并保存真实流式日志，脚本限定只读工具，不跳过全局权限检查。

`evidence/01-skill.png`、`02-hook.png`、`03-mcp.png` 是**真实 CLI 日志的浏览器可读视图截图**，不是原生终端截图。原始 `claude-session.jsonl`、Hook 日志、MCP 日志及生成代码一并提供，便于核对。若老师明确要求原生终端界面，可按上述命令复现后截图。

配置参考官方文档：[Skills](https://code.claude.com/docs/en/skills)、[Hooks](https://code.claude.com/docs/en/hooks)、[MCP](https://code.claude.com/docs/en/mcp)。

本项目仅用于本地课程作业，不是多用户生产服务。WebMCP 增强会在支持该接口的浏览器中注册，当前 Edge 不支持该提议接口，未进行原生 WebMCP 验证；不影响作业要求的 stdio MCP。
