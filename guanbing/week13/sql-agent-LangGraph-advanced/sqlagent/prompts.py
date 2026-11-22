"""提示词层：把原实现散落在各函数里的字符串，收拢成 `ChatPromptTemplate`。

**逐字保留原文**：所有提示词正文与原实现一字不差地搬运 —— 换框架不是改提示词，
否则「重写前后行为是否等价」就无从对照了。这里只做两件事：
1. 把 `f"...{q}..."` 手工拼接换成 `ChatPromptTemplate.from_messages([...])` 的命名变量；
2. 按用途分类（流水线 / 闸门 / 裁判 / 造题），供 `workflow`、`agents`、`judge` 三条路共用。

用法：
```python
from sqlagent import llm, prompts
msgs = prompts.SCOPE_PROMPT.format_messages(catalog=..., question=...)
judgement = llm.invoke_structured_messages(ScopeJudgement, msgs)
```
"""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

# --------------------------------------------------------------------------- #
# 流水线：选表 / 生成 / 改写 / 汇总
# --------------------------------------------------------------------------- #

SELECT_TABLES_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "你是一个专业的 SQL 专家。请从候选表里选出回答该问题所需的最小表集合。"),
    ("human",
     "用户问题：{question}\n\n"
     "候选表：\n{catalog}\n\n"
     "表间外键关系：\n{relations}\n\n"
     '只输出 JSON：{{"tables": ["表名", ...]}}'),
])

GENERATE_SQL_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "你是一个专业的 SQLite 专家。只生成一条只读 SELECT 语句，不要解释，不要写多语句。"),
    ("human",
     "用户问题：{question}\n\n"
     "相关表信息：\n{schema}\n\n"
     "请只输出 SQL（可带分号）："),
])

REWRITE_SQL_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "你是一个专业的 SQLite 专家。上一次的 SQL 执行失败，请根据报错修正。只输出修正后的单条只读 SELECT。"),
    ("human",
     "用户问题：{question}\n\n"
     "相关表信息：\n{schema}\n\n"
     "上一次 SQL：\n{sql}\n\n"
     "报错信息：\n{error}\n\n"
     "请输出修正后的 SQL："),
])

SUMMARIZE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "你是一个数据分析助手。请依据 SQL 执行结果，用简洁自然语言回答问题，不要编造数据。"),
    ("human",
     "用户问题：{question}\n\n"
     "执行的 SQL：\n{sql}\n\n"
     "执行结果（共 {total} 行，最多展示 {max_rows} 行）：\n{rows}\n\n"
     "请给出自然语言结论："),
])

# --------------------------------------------------------------------------- #
# 闸门 [B]：范围判定（守门人）
# --------------------------------------------------------------------------- #

SCOPE_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "你是数据库助手的守门人。用户只能询问 chinook 音乐商店数据库里的数据。"
     "请判断用户问题是否可答，并只输出 JSON。"),
    ("human",
     "数据库包含以下表与字段：\n{catalog}\n\n"
     "用户问题：{question}\n\n"
     "请只输出 JSON：\n"
     '{{"scope": "in" | "out" | "ambiguous",\n'
     '  "reason": "简短理由",\n'
     '  "tables": ["若为 in，给出可能用到的表名"],\n'
     '  "clarify": "若为 ambiguous，给出向用户的反问；否则空字符串"}}\n\n'
     "判定标准：\n"
     "- in：用上面的表 / 字段能查出来。\n"
     "- out：与这个数据库无关（闲聊、别的领域、需要库外数据）。\n"
     "- ambiguous：意图不完整或有歧义，需要向用户澄清。"),
])

# --------------------------------------------------------------------------- #
# 闸门 [A]：追问改写
# --------------------------------------------------------------------------- #

REWRITE_FOLLOWUP_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "你是一个查询改写助手。请结合对话历史，把用户的最新提问改写成一条不依赖上下文、"
     "可独立执行的完整问题。只输出改写后的问题。"),
    ("human", "对话历史：\n{history}\n\n最新提问：{question}\n\n请输出改写后的独立问题："),
])

# 历史滚动摘要：会话变长后，把「较早的对话」压成一段背景，取代「只看最近 5 轮」的硬截断。
# 只在对话足够长时才被调用（见 conversation/graph.py 的 node_summarize），短对话零开销。
SUMMARIZE_HISTORY_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "你是一个对话压缩助手。请把较早的多轮对话压成一段简明的背景摘要，供后续追问改写参考。"
     "只保留与数据查询相关的事实：用户问过什么、涉及哪些表 / 指标 / 口径、已确认的结论。"
     "不要编造，不要保留寒暄，不要输出多余解释，只输出摘要正文。"),
    ("human",
     "已有摘要（可能为空）：\n{previous}\n\n"
     "需要并入摘要的较早对话：\n{turns}\n\n"
     "请输出更新后的背景摘要："),
])

# --------------------------------------------------------------------------- #
# 逐层裁判（10.3 LLM 裁判，带参照物）
# --------------------------------------------------------------------------- #

JUDGE_SQL_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "你是严格的 SQL 语义审校员。请判断两条 SQL 在给定问题上是否语义等价："
     "忽略别名、写法、列顺序差异，只比较过滤条件 / 连接关系 / 聚合口径 / 排序与取值。"
     "只输出 JSON。"),
    ("human",
     "用户问题：{q}\n\n"
     "待审 SQL：\n{sql}\n\n"
     "权威参考 SQL：\n{ref}\n\n"
     '请只输出 JSON：\n{{"equivalent": true, "differences": ["若不等价，列出关键口径差异"], "score": 1.0}}'),
])

JUDGE_TABLES_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "你是严格的选表审校员。判断「多选的表」是否会使结果口径改变或出错。只输出 JSON。"),
    ("human",
     "用户问题：{q}\n"
     "权威所需表：{want}\n"
     "实际多选的表：{extra}\n"
     "本次选中的全部表：{got}\n\n"
     "多选这些表是否会导致结果口径改变 / 出错？请只输出 JSON：\n"
     '{{"harmless": true, "reason": "简短理由"}}'),
])

JUDGE_RESULT_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "你是严格的结果集审校员。请判断「助手结果」与「权威结果」在回答同一个问题时是否语义一致。"
     "以下都**不算**差异：列被拆分或合并（如把 FirstName、LastName 合写成全名）、别名不同、"
     "行序不同、列顺序不同、数值格式不同。只有过滤口径、聚合口径、取值口径不一致才算差异。"
     "只输出 JSON。"),
    ("human",
     "用户问题：{q}\n\n"
     "权威结果（参考答案）：{want}\n\n"
     "助手结果：{got}\n\n"
     "请判断两者在回答该问题上是否语义一致。只输出 JSON：\n"
     '{{"equivalent": true, "differences": ["若不一致，列出关键口径差异"], "reason": "简短理由"}}'),
])

JUDGE_ANSWER_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "你是严格的结果校对员。请判断「助手回答」是否忠实覆盖了「参考要点」，"
     "且没有编造与权威执行结果冲突的数字。只输出 JSON。"),
    ("human",
     "用户问题：{q}\n\n"
     "权威执行结果：{rows}\n\n"
     "参考要点：{kp}\n\n"
     "助手回答：\n{ans}\n\n"
     "请只输出 JSON：\n"
     '{{"covered": ["已覆盖的要点"], "missing": ["遗漏的要点"],\n'
     '  "faithful": true, "reason": "简短理由", "score": 1.0}}'),
])

# --------------------------------------------------------------------------- #
# 问题模拟生成器（造题）
# --------------------------------------------------------------------------- #

SIMULATE_QUESTION_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "你是数据库专家。"),
    ("human",
     "你是一个数据库专家。请针对下面这张表，提出一个{style_desc}的自然语言问题。\n"
     "只输出问题本身，不要有任何多余内容。\n\n"
     "表名称：{table}\n"
     "字段：{fields}\n"
     "表样例：\n{sample}\n"
     "表结构：\n{schema}\n"),
])

SIMULATE_SQL_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "你是 SQLite 专家。"),
    ("human",
     "你是一个 SQLite 专家。请针对以下问题和表，生成一条只读 SELECT。只输出 SQL，不要解释。\n\n"
     "表名称：{table}\n"
     "字段：{fields}\n"
     "表样例：\n{sample}\n"
     "表结构：\n{schema}\n"
     "问题：{question}\n"),
])

SIMULATE_REWRITE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "你是提问改写助手。"),
    ("human",
     "请把下面这个偏技术的提问，改写成普通用户会问的口吻，不要出现表名 / 字段名等术语。\n"
     "只输出改写后的提问。\n\n"
     "原始问题：{question}\n"
     "涉及的表：{table}\n"),
])

SIMULATE_OUT_OF_SCOPE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "你是测试数据生成器。"),
    ("human",
     "请生成 {n} 个与「音乐商店销售数据库」完全无关的自然语言问题"
     '（如天气、闲聊、别的领域）。只输出 JSON：{{"questions": ["...", "..."]}}'),
])

# --------------------------------------------------------------------------- #
# 英文变体（中英双语，见 README「中英双语」）
# --------------------------------------------------------------------------- #
# 只给「**产出面向用户的自然语言**」的提示词配英文版：
# - 汇总（SUMMARIZE）→ 最终回答；
# - 范围判定（SCOPE）→ 越界理由 + 澄清反问；
# - 追问改写（REWRITE_FOLLOWUP）→ 前端展示的「独立问题」；
# - 历史摘要（SUMMARIZE_HISTORY）→ 喂给改写器的背景。
#
# 选表 / 生成 / 改写这三条**故意不配**英文版：它们的产物是表名与 SQL，与自然语言无关，
# 同一份提示词在中英文提问下都成立。少一份变体，就少一处会漂移的「第二事实源」。
# 中文正文与之逐字不变 —— 这是 §4 的「提示词逐字沿用」不变量。

SUMMARIZE_PROMPT_EN = ChatPromptTemplate.from_messages([
    ("system", "You are a data analysis assistant. Based on the SQL result, answer the user's "
               "question in concise natural language. Never fabricate data."),
    ("human",
     "User question: {question}\n\n"
     "Executed SQL:\n{sql}\n\n"
     "Result ({total} rows, showing up to {max_rows}):\n{rows}\n\n"
     "Give your conclusion in natural language:"),
])

SCOPE_PROMPT_EN = ChatPromptTemplate.from_messages([
    ("system",
     "You are the gatekeeper of a database assistant. The user may only ask about data in the "
     "chinook music-store database. Decide whether the question is answerable, and output JSON only."),
    ("human",
     "The database contains the following tables and columns:\n{catalog}\n\n"
     "User question: {question}\n\n"
     "Output JSON only:\n"
     '{{"scope": "in" | "out" | "ambiguous",\n'
     '  "reason": "short reason",\n'
     '  "tables": ["if in, the tables that may be needed"],\n'
     '  "clarify": "if ambiguous, the question to ask back; otherwise an empty string"}}\n\n'
     "Criteria:\n"
     "- in: answerable with the tables / columns above.\n"
     "- out: unrelated to this database (chit-chat, other domains, needs outside data).\n"
     "- ambiguous: the intent is incomplete or unclear and needs clarification."),
])

REWRITE_FOLLOWUP_PROMPT_EN = ChatPromptTemplate.from_messages([
    ("system",
     "You are a query-rewriting assistant. Using the conversation history, rewrite the user's "
     "latest question into a complete, self-contained question that can run without any context. "
     "Output only the rewritten question."),
    ("human", "Conversation history:\n{history}\n\nLatest question: {question}\n\n"
              "Output the rewritten standalone question:"),
])

SUMMARIZE_HISTORY_PROMPT_EN = ChatPromptTemplate.from_messages([
    ("system",
     "You are a conversation-compression assistant. Compress the earlier turns into a concise "
     "background summary for later follow-up rewriting. Keep only facts relevant to data queries: "
     "what the user asked, which tables / metrics / definitions were involved, which conclusions "
     "were confirmed. Do not fabricate, drop pleasantries, and output only the summary body."),
    ("human",
     "Existing summary (may be empty):\n{previous}\n\n"
     "Earlier turns to merge into the summary:\n{turns}\n\n"
     "Output the updated background summary:"),
])


def summarize_prompt(lang: str = "zh") -> ChatPromptTemplate:
    """汇总提示词（最终回答的语言由它决定）。"""
    return SUMMARIZE_PROMPT_EN if lang == "en" else SUMMARIZE_PROMPT


def scope_prompt(lang: str = "zh") -> ChatPromptTemplate:
    """范围判定提示词（越界理由与澄清反问的语言由它决定）。"""
    return SCOPE_PROMPT_EN if lang == "en" else SCOPE_PROMPT


def rewrite_followup_prompt(lang: str = "zh") -> ChatPromptTemplate:
    """追问改写提示词（前端展示的「独立问题」的语言由它决定）。"""
    return REWRITE_FOLLOWUP_PROMPT_EN if lang == "en" else REWRITE_FOLLOWUP_PROMPT


def summarize_history_prompt(lang: str = "zh") -> ChatPromptTemplate:
    """历史滚动摘要提示词。"""
    return SUMMARIZE_HISTORY_PROMPT_EN if lang == "en" else SUMMARIZE_HISTORY_PROMPT
