"""提示词（与代码分离）。

四组提示词对应 agentic 循环的四个 LLM 步骤：
- plan        规划：拆子问题
- extract     抽取：从来源提取证据
- decide      补检：判断信息是否充分
- synthesize  综合：生成报告
"""
from __future__ import annotations

from .. import config

SYSTEM_PROMPT = (
    "你是一名严谨的研究助手。你只依据给定的检索来源回答问题，"
    "绝不编造来源；无法从来源得到的结论应明确标注为推断。"
)


def _messages(user: str, system: str = SYSTEM_PROMPT) -> list[dict]:
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def plan(topic: str) -> list[dict]:
    """规划：把主题拆成 3~5 个子问题。"""
    user = f"""研究主题：{topic}

请把该主题拆分为 {config.MIN_SUB_QUESTIONS}~{config.MAX_SUB_QUESTIONS} 个子问题，
用于逐个子问题检索资料。子问题应相互独立、覆盖主题的关键维度。

只输出 JSON（不要其他文字）：
{{"sub_questions": [{{"question": "子问题", "rationale": "为什么拆这个子问题"}}]}}"""
    return _messages(user)


def extract(sub_question: str, numbered_sources: str) -> list[dict]:
    """抽取：从来源列表中提取与子问题相关的证据。"""
    user = f"""子问题：{sub_question}

以下是检索到的来源（编号从 0 开始）：
{numbered_sources}

请从以上来源中抽取与子问题相关的关键结论/证据。
只输出 JSON（不要其他文字）：
{{"evidences": [{{"claim": "结论", "source_index": 0, "quote": "原文引用"}}]}}

规则：
- source_index 必须严格等于某条来源前面的 [编号]（0 到 编号最大值之间的整数），不要使用正文内容里出现的其他数字。
- 若结论是结合常识的推断而非来自具体来源，source_index 用 null。
- quote 尽量摘录原文关键词句；若无法摘录可为空字符串。"""
    return _messages(user)


def decide(topic: str, evidence_dump: str, round_no: int) -> list[dict]:
    """补检判定：现有信息是否足够，是否需要补充检索。"""
    user = f"""研究主题：{topic}

目前已收集的证据（第 {round_no} 轮检索后）：
{evidence_dump}

请判断：现有信息是否已足够生成一份可信的报告？
- 若仍有关键维度缺失或证据不足，需要补充检索，则给出需要检索的新关键词。
- 若已足够，则结束检索。

只输出 JSON（不要其他文字）：
{{"need_more": true, "reason": "缺失的维度", "new_queries": ["补充检索关键词"]}}"""
    return _messages(user)


def synthesize(topic: str, evidence_dump: str) -> list[dict]:
    """综合：生成结构化报告。"""
    user = f"""研究主题：{topic}

已收集的证据（含来源编号）：
{evidence_dump}

请综合生成一份带来源引用的研究报告。
只输出 JSON（不要其他文字）：
{{
  "summary": "摘要",
  "sections": [{{"heading": "分节标题", "content": "分节正文（Markdown，引用来源时用 [编号] 标注）"}}],
  "key_conclusions": ["关键结论1", "关键结论2"],
  "open_questions": ["遗留问题1"],
  "confidence": {{"level": "high|medium|low", "cutoff": "信息截止时间", "notes": ["置信度说明"]}}
}}

要求：
- sections 中引用具体来源时，必须用 [来源N] 标注，N 只能是上文「来源列表」里出现过的编号（从 0 开始且 < 来源总数）。
- 严禁使用来源列表编号之外的数字作为来源引用；无法溯源的内容不标引用，或明确写「据推断」。
- key_conclusions 是全文最重要的 3~8 条结论。
- confidence.level 依据来源数量、来源时效性、是否有推断而定。"""
    return _messages(user)
