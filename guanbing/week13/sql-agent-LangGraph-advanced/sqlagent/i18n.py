"""中英双语文案层：把面向用户的**固定话术**从逻辑里抽出来，按语言取用。

为什么单独放一个模块：

- 这些句子是「业务分支」的一部分（范围外谢绝、改写到上限放弃、澄清超限收尾、人工复核的
  兜底理由……），散落在各图节点里既难维护、也没法整体切换语言；
- 抽出来之后，`lang` 只是「取哪套文案」的一个参数 —— 图的**拓扑、节点数、LLM 调用次数
  完全不受影响**（这是 `DESIGN.md` §4 不变量的要求：加语言不是加行为）。

**默认语言 `zh`**：核心库的一切默认行为逐字不变（含 `FAIL_ANSWER` 等既有模块级常量），
所以既有单测原样通过。产品侧（网页）默认 `en`，由边界层在建成会话时显式传入 ——
见 `README.md` 的「中英双语」一节。

更长、需要并排对照的**提示词**英文变体放在 `prompts.py`；本模块只收**短句**。
"""

from __future__ import annotations

LANGS = ("zh", "en")
DEFAULT_LANG = "zh"

# 名称 → {语言: 文本}。新增文案时中英各写一条，缺哪条就会退回 zh。
_TEXT: dict[str, dict[str, str]] = {
    # 数据流水线：放弃（改写到上限仍未通过校验）
    "fail_answer": {
        "zh": "抱歉，未能生成可执行的 SQL，已放弃（不编造结果）。",
        "en": "Sorry — I couldn't produce a runnable SQL, so I stopped here "
              "(no made-up results).",
    },
    # 会话图：澄清反问次数用尽后的收尾
    "clarify_limit_answer": {
        "zh": "抱歉，我连着问了几次也没能确定你想查什么，这一轮就先到这里。"
              "换个更具体的问法会更好，比如「统计每个国家的客户数」。",
        "en": "Sorry — I asked a few times but still couldn't pin down what you want, "
              "so let's stop this round. A more specific question works better, e.g. "
              "\"How many customers are there in each country?\"",
    },
    # 范围闸门：越界谢绝（`{reason}` 由守门人给出）
    "out_of_scope": {
        "zh": "抱歉，这个问题超出了当前 chinook 数据库的范围：{reason}\n"
              "你可以问我销量 / 客户 / 员工 / 曲目 / 专辑 / 流派等数据问题。",
        "en": "Sorry, this question is outside the current chinook database: {reason}\n"
              "Try asking about sales / customers / employees / tracks / albums / genres.",
    },
    # 范围闸门：问不出所以然时的兜底反问
    "clarify_fallback": {
        "zh": "能再具体一点吗？",
        "en": "Could you be more specific?",
    },
    # 范围闸门：结构化输出解析失败 / 判 in 却落不到任何真实表
    "scope_unparsable": {
        "zh": "判定输出无法解析",
        "en": "The scope judgement could not be parsed",
    },
    "scope_no_table_reason": {
        "zh": "无法确定要查询的表",
        "en": "Could not determine which table to query",
    },
    "scope_no_table_clarify": {
        "zh": "我无法确定要查询哪张表，能补充一下吗？",
        "en": "I can't tell which table to query — could you add a bit more detail?",
    },
    # 追问转澄清时拼在反问前面的引导语
    "need_clarify_prefix": {
        "zh": "我需要先澄清一下：",
        "en": "Let me clarify first: ",
    },
    # 拼进提示词的「对话历史」角色标签（含分隔符，中英一套到底）
    "history_user_label": {
        "zh": "用户：",
        "en": "User: ",
    },
    "history_assistant_label": {
        "zh": "助手：",
        "en": "Assistant: ",
    },
    "history_empty": {
        "zh": "（暂无）",
        "en": "(none yet)",
    },
    # 人工复核：两种兜底理由（会作为 `trace.error` 显示）
    "review_rejected": {
        "zh": "人工复核驳回",
        "en": "Rejected during human review",
    },
    "review_edit_missing_sql": {
        "zh": "人工要求改写却未提供 SQL",
        "en": "An edit was requested in human review but no SQL was provided",
    },
}

assert set(_TEXT) and all(set(v) == set(LANGS) for v in _TEXT.values())


def text(name: str, lang: str = DEFAULT_LANG) -> str:
    """按语言取一条固定话术；未知语言回落到 `zh`（默认行为不变）。"""
    entry = _TEXT[name]
    return entry.get(lang) or entry[DEFAULT_LANG]


def normalise(lang: str | None) -> str:
    """把外部传入的语言名收敛到 `LANGS` 之一。

    容错：大小写、`en-US` / `zh-Hans` 这类区域后缀都吃掉；认不出的按默认 `zh` 处理。
    """
    value = (lang or "").strip().lower()
    for known in LANGS:
        if value == known or value.startswith(f"{known}-") or value.startswith(f"{known}_"):
            return known
    return DEFAULT_LANG
