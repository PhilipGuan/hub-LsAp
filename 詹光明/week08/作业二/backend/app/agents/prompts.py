"""全部 prompt 模板。

**模板一律用 ``string.Template``（``$var``），不用 ``str.format``。**
prompt 里到处是 JSON 示例的大括号，``.format()`` 会把它们当占位符炸掉。
这个约定必须从第一天定死，否则以后每加一个 JSON 示例就炸一次。
"""

from __future__ import annotations

from string import Template

from ..models import Note, Source, SubQuestion

# ══════════════════════════════════════════════════════════════
# 共享引用规则 —— 被 reader / synthesizer 复用
# ══════════════════════════════════════════════════════════════

CITATION_RULES = """引用规则（必须严格遵守）：
1. 每一条事实性陈述后面必须紧跟来源编号，格式为 [S3]。多个来源写 [S1][S4]。
2. 编号**只能**从下面给出的可用来源列表里选，禁止自己编造编号。
3. 没有来源支撑的判断，必须在句首写「（模型推断）」四个字，不许伪装成有来源。
4. 禁止用「有研究表明」「业内普遍认为」这类说法代替具体引用。
5. 不要复述编号对应的 URL —— 报告末尾会统一给出来源列表。"""


def render_sources(sources: list[Source], *, max_note_len: int = 300) -> str:
    """把可用来源渲染成给 LLM 看的清单。

    只列 ``readable`` 的来源 —— 没抓到正文、也没搜索摘要的来源不配被引用。
    正文按 ``max_note_len`` 截断，避免 prompt 被长文撑爆。
    """
    if not sources:
        return "（无可用来源）"

    lines: list[str] = []
    for source in sources:
        origin = source.content_origin.value
        site = source.site_name or source.domain or "未知"
        lines.append(f"[{source.sid}] {source.title or '（无标题）'}")
        lines.append(f"     站点: {site} | 正文来路: {origin}")
        if source.published_at:
            lines.append(f"     发布时间: {source.published_at.strftime('%Y-%m-%d')}")
        body = (source.content or source.snippet or "").strip()
        if body:
            lines.append(f"     内容: {body[:max_note_len]}")
        lines.append("")
    return "\n".join(lines)


def render_sub_questions(sub_questions: list[SubQuestion]) -> str:
    if not sub_questions:
        return "（无）"
    return "\n".join(f"- [{q.qid}] {q.text}" for q in sub_questions)


def render_notes(notes: list[Note], *, max_per_qid: int = 40) -> str:
    """把笔记渲染成给 synthesizer 看的材料，按子问题分组。"""
    if not notes:
        return "（无）"

    grouped: dict[str, list[Note]] = {}
    for note in notes:
        grouped.setdefault(note.qid or "未归类", []).append(note)

    blocks: list[str] = []
    for qid, items in grouped.items():
        blocks.append(f"### 子问题 {qid}")
        for note in items[:max_per_qid]:
            weak = "（弱证据）" if note.weak else ""
            blocks.append(f"- [{note.sid}]{weak} {note.claim}")
            if note.evidence:
                blocks.append(f"    依据: {note.evidence[:200]}")
        blocks.append("")
    return "\n".join(blocks)


# ══════════════════════════════════════════════════════════════
# planner
# ══════════════════════════════════════════════════════════════

PLANNER_PROMPT = Template(
    """你是一名严谨的研究规划员。请把下面的研究主题拆成若干**互不重叠**的子问题，
并为每个子问题给出可以直接丢进搜索引擎的检索词。

研究主题：$topic

要求：
1. 拆出 $max_sub_questions 个子问题，覆盖不同侧面：
   现状 / 关键角色 / 数据与事实 / 争议与风险 / 趋势 里挑几个，不必全用。
2. 子问题之间不要互相包含，避免后面检索出同一批网页。
3. 每个子问题给 1~2 个检索词，**检索词要短**（不超过 25 个字），像人在搜索框里打的那样，
   不要写成疑问句，不要带「请问」「是什么」这类词。
4. 总共给出不超过 $queries_per_round 个首轮检索词，按重要性排序。

只输出 json，结构如下：
{
  "sub_questions": [
    {"text": "子问题内容", "rationale": "为什么要研究它", "queries": ["检索词1", "检索词2"]}
  ],
  "queries": ["首轮检索词1", "首轮检索词2"],
  "rationale": "整体规划思路，一到两句话"
}"""
)


# ══════════════════════════════════════════════════════════════
# reader
# ══════════════════════════════════════════════════════════════

READER_PROMPT = Template(
    """你是一名严谨的资料员。请从下面这一条网页内容里，抽取**事实性主张**。

当前正在回答的子问题：
$sub_questions

网页标题：$title
站点：$site_name
正文来路：$origin（page=真实网页正文，search_summary=搜索引擎生成的长摘要，snippet=搜索列表里的一行描述）
网页内容：
─────
$content
─────

要求：
1. 只抽取**这篇内容里确实说了的**事实、数据、结论。不要补充你自己的知识。
2. 每条主张要能在原文里找到依据，把依据原文片段放进 evidence。
3. 与上面子问题无关的内容不要抽。
4. strength 表示这条证据的可信程度（0~1）：有具体数据/官方口径给高分，
   泛泛而谈、明显的营销文案给低分。
5. **如果这篇内容跟子问题无关，或者没有可用的事实**，就返回空的 notes 列表 ——
   宁可空手而归，也不要硬凑。返回空是完全可以接受的。
6. quote 填原文里最能支撑该主张的一句话（照抄，不要改写）。

只输出 json，结构如下：
{
  "notes": [
    {"claim": "一条事实主张", "evidence": "支撑它的原文摘要", "quote": "原文原句", "strength": 0.8}
  ],
  "summary": "这篇内容整体讲了什么，一句话"
}"""
)


# ══════════════════════════════════════════════════════════════
# reflector
# ══════════════════════════════════════════════════════════════

REFLECTOR_PROMPT = Template(
    """你是一名研究进度审查员。请判断当前收集到的材料是否足以回答研究主题，
并指出还缺什么。

研究主题：$topic
已经进行到第 $round 轮（最多 $max_rounds 轮）。

子问题及其覆盖情况：
$coverage

已经收集到的笔记：
$notes

已经检索过的关键词（**不要重复生成这些词或它们的近义改写**）：
$used_queries

要求：
1. 逐个判断每个子问题是否已经被充分回答。**只有在材料确实能支撑结论时才算够。**
2. 找出真正缺失的信息，写成具体的 gap，不要写「还需要更多资料」这种空话。
3. 针对每个 gap 给出下一轮检索词：**必须与已用过的关键词明显不同**，
   换角度、换措辞、换实体名称，而不是把原词改个字。
4. 如果材料已经足够，sufficient 给 true，next_queries 给空列表。
5. 不要因为「已经搜过几轮了」就判定足够 —— 只看材料本身够不够。

只输出 json，结构如下：
{
  "sufficient": false,
  "coverage": {"q1": 0.8, "q2": 0.3},
  "gaps": [{"qid": "q2", "gap": "缺少具体缺什么"}],
  "next_queries": [{"qid": "q2", "query": "下一轮检索词", "reason": "为什么这样搜"}],
  "rationale": "整体判断，一到两句话"
}"""
)


# ══════════════════════════════════════════════════════════════
# synthesizer
# ══════════════════════════════════════════════════════════════

SYNTHESIZER_HEAD_PROMPT = Template(
    """你是一名研究报告撰写者。请根据下面已经收集并核实过的材料，
为这份研究写标题、摘要、关键结论和遗留问题。

研究主题：$topic
信息截止时间：$as_of

可用来源清单：
$sources

已收集的材料（每条都标了来源编号）：
$notes

$citation_rules

要求：
1. 标题要具体，能看出研究对象，不要写「研究报告」这种空标题。
2. 摘要 150~300 字，概括最重要的发现，**不要写「本文将……」这类套话**。
3. 关键结论 3~6 条，每条都是可以独立成立的判断，不是「研究了什么」的过程描述。
   每条结论在 sids 里列出支撑它的来源编号（只填编号，如 ["S1", "S3"]）。
   **一条都找不到来源支撑的结论，sids 给空列表** —— 会被标成模型推断，这是允许的。
4. 遗留问题 2~5 条，写明这次研究**没能回答**什么，以及为什么没能回答。
5. 材料不足以支撑的推断，明确写「（模型推断）」，不要伪装成有来源的结论。

只输出 json，结构如下：
{
  "title": "报告标题",
  "executive_summary": "摘要正文",
  "key_conclusions": [{"text": "结论内容", "sids": ["S1", "S2"]}],
  "open_questions": ["遗留问题一", "遗留问题二"]
}"""
)


SYNTHESIZER_SECTION_PROMPT = Template(
    """你是一名研究报告撰写者。请针对下面**这一个**子问题写正文。

研究主题：$topic
本次要写的子问题：$question

可用来源清单：
$sources

与该子问题相关的材料：
$notes

$citation_rules

要求：
1. 直接写正文，300~600 字，用 markdown 段落。**不要重复写一级标题** ——
   标题由外层统一处理，你只写正文。
2. 每个事实性陈述后面跟来源编号，编号**只能**从上方的来源清单里选。
3. 材料里没有的内容不要写。如果材料实在太少，就如实写「现有材料未能充分回答该问题」，
   并说明缺什么 —— **不要靠常识硬凑**。
4. 不要写「综上所述」「总而言之」这类收尾套话。

只输出 json，结构如下：
{
  "heading": "这一节的标题",
  "body_md": "这一节的正文（markdown）"
}"""
)


__all__ = [
    "CITATION_RULES",
    "PLANNER_PROMPT",
    "READER_PROMPT",
    "REFLECTOR_PROMPT",
    "SYNTHESIZER_HEAD_PROMPT",
    "SYNTHESIZER_SECTION_PROMPT",
    "render_notes",
    "render_sources",
    "render_sub_questions",
]
