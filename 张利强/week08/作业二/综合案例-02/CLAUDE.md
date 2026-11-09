# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目：深度研究助手（week08 作业二 · 综合案例-02）

输入一个研究主题，自动完成：拆子问题规划 → 多轮检索 → 阅读抽取 → 判断是否补检 → 综合生成，最终产出四件套：

1. 结构化研究报告（摘要 / 分节正文 / 关键结论 / 遗留问题）
2. 来源列表（每条结论关联 URL / 标题 / 来源，可追溯）
3. 研究过程记录（检索词、阅读页面、迭代轮数）
4. 置信度说明（可靠程度、信息截止时间；无来源结论标注"模型推断"）

**需求规格以 README.md 为唯一来源**，实现时以它为准；本文件不复述业务背景。

## 当前状态（截至 2026-09-11）

项目已完整实现：深度研究助手 - 输入研究主题，自动完成"规划→检索→阅读→判断→综合"五步流程，输出四件套（结构化报告、来源列表、研究过程、置信度说明）。

### 技术栈
- **语言**: Python 3.8+
- **CLI 工具**: 命令行启动（`run_research.py` 或 `python -m src.main`）
- **搜索引擎**: Bocha Web Search API
- **报告生成**: Claude 3 API
- **网页解析**: BeautifulSoup4
- **并发处理**: asyncio + aiohttp

### 核心模块
1. `planner.py` - 智能问题规划，将主题拆解为子问题
2. `searcher.py` - Bocha API 封装，支持批量搜索
3. `reader.py` - 网页内容提取，支持 BeautifulSoup 解析
4. `judge.py` - 内容质量评估，智能判断是否补检
5. `synthesizer.py` - Claude API 报告生成
6. `utils.py` - 重试机制、日志、会话管理

### 运行命令
```bash
# 基础用法
python run_research.py "研究主题"

# 带参数
python run_research.py "人工智能发展趋势" --max-rounds 4 --max-pages 15

# 原生入口
python -m src.main "研究主题" --bocha-key $KEY --claude-key $KEY
```

### 依赖安装
```bash
pip install -r requirements.txt
cp .env.example .env  # 配置 API Key
```

### 断点续存功能
- **自动保存**：每轮检索完成后保存进度到 `data/sessions/`
- **智能恢复**：支持从中断的轮次继续研究
- **数据完整性**：保存所有搜索结果、内容和子问题
- **错误处理**：自动处理不完整的会话数据

## 外部依赖：博查（Bocha）Web Search API

README 指定的唯一搜索工具。官方文档（飞书 wiki，可能需登录）：https://bocha-ai.feishu.cn/wiki/RXEOw02rFiwzGSkd9mUcqoeAnNK

- 端点：`POST https://api.bocha.cn/v1/web-search`
- 认证：`Authorization: Bearer <key>`；key 已明文存在于 README.md，使用时从那里或环境变量读取，勿在其他文件再复制明文
- 请求体（已验证的参数）：`{"query": "...", "summary": true, "count": 10}`
- 响应结构（2026-09-10 实测）：
  - 搜索结果位于 `data.webPages.value[]`，每条字段：`name`（标题）、`url`、`displayUrl`、`snippet`、`summary`、`siteName`、`siteIcon`、`datePublished`（ISO 8601 带时区）、`dateLastCrawled`
  - 顶层有 `code`（字符串 `"200"`）、`msg`、`log_id`；`data.images`、`data.videos` 基本为空，可忽略
- **关键实测结论：`summary` 与 `snippet` 内容与长度相近（约百字摘要级），不是网页正文。** "阅读抽取"环节若需正文深度，必须另建网页抓取/正文抽取层，不能依赖搜索 API 的返回
- `datePublished` 通常有值，可用于来源列表的发布日期与"信息截止时间"标注

## 仓库上下文

- 本目录不是 git 仓库根：git root 是上层的 `hub-AIStudy` 仓库（包含各周次作业），git 操作会影响整个课程仓库
- 历史提交惯例：`张利强 weekN作业`
- `skills/` 与 `.claude/skills/` 存放课程提供的 Claude Code 技能（grilling、ponytail），与本项目业务代码无关，不要当作项目源码修改
