# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 这个目录是什么

Week08「Agent 开发与 Vibe Coding 基础」的 Claude Code 实操工作区。当前为空——代码尚未落地，本文件是占位，代码落地后需按下方「待补充」章节扩展。

作业目标（见 `../作业.md`）：

1. 配置 Claude Code，练习 skill / hook / mcp（产出为截图，不落本目录）。
2. 用 Vibe Coding 自己实现「综合案例 2 · 深度研究助手」。

## 规格与参考（实现前先读，是唯一规格来源）

- 规格 / 产品需求：`../Week08/Part2-VibeCoding实操/综合案例-02/README.md`
- 参考实现（后端已实现，前端未实现）：`../Week08/Part2-VibeCoding实操/综合案例-02/`
- 参考实现的 CLAUDE.md（架构 / 命令 / 编码约定）：`../Week08/Part2-VibeCoding实操/综合案例-02/CLAUDE.md`
- 课程示范如何写 CLAUDE.md：`../Week08/Part2-VibeCoding实操/04-上下文管理与CLAUDE.md/CLAUDE.md`

深度研究助手：输入一个研究主题，产出带来源引用的研究报告——四类产物：结构化报告、来源列表、研究过程记录、置信度说明。核心是 agentic 研究循环（规划 → 多轮检索 → 阅读抽取 → 判断是否补检 → 综合生成报告），**不是**一次性问答；可追溯是硬要求。

## 待补充（代码落地后填写）

- 常用命令：安装依赖、启动后端、发起/轮询研究、冒烟验证。
- 架构：`backend/` 各模块职责与数据流（app → research → engine → agent/ + tools → storage）。
- 编码约定：沿用参考项目的规范（pydantic 模型集中在 `models.py`、提示词与代码分离、每个模块 `__main__` demo、标准库 `logging` 中文日志等），具体见参考实现的 CLAUDE.md。
