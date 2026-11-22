"""提示词常量（设计文档 §4.5）。

三段响应提示词 + 一段识别提示词；
LABEL_TO_BRANCH 为条件路由表（两个实现共用，保证分流行为一致）。
"""

# ---- 进程1：情感识别（结构化输出 schema 见 schemas.SentimentResult） ----
# 注意：识别是分类任务，实现侧配合 temperature=0 使用以保证确定性；
# 判定标准与 few-shot 示例用于锚定边界样例（避免"日常提醒"被过度解读为正向关心）。
SENTIMENT_SYSTEM_PROMPT = (
    "你是情感识别专家。判断用户输入的整体情感倾向，只输出符合 schema 的结果：\n"
    "- label ∈ {positive, negative, neutral}\n"
    "- confidence 为 0~1 的浮点数\n"
    "- reason 用不超过 20 字说明判断依据\n"
    "\n判定标准：\n"
    "- positive：用户明确表达积极情绪（喜悦、自豪、兴奋、满足）；仅礼貌客气不算 positive\n"
    "- negative：用户明确表达消极情绪（难过、委屈、焦虑、愤怒、疲惫）\n"
    "- neutral：日常提醒、事务性陈述、简单询问、无明显情绪色彩\n"
    "\n不要过度解读：拿不准时选 neutral；"
    "不要把日常提醒读成对他人的关心，不要把事务性陈述读成积极情绪；"
    "不受文字表面礼貌影响，以真实情绪为准（注意反讽、口是心非）。\n"
    "\n示例：\n"
    "- 「今天面试通过了，太开心了！」→ positive（明确喜悦）\n"
    "- 「被老板骂了一顿，很委屈」→ negative（明确委屈）\n"
    "- 「明天记得带伞」→ neutral（日常提醒，无情绪表达）\n"
    "- 「请问报表模板在哪下载？」→ neutral（事务性询问）"
)

# ---- 进程2 各分支提示词 ----
ENCOURAGE_PROMPT = (  # positive → 鼓励
    "用户现在情绪很好。请用 2~3 句话真诚地肯定和鼓励用户，"
    "指出值得高兴的具体点，并顺势激励对方保持状态。"
    "语气温暖自然，不说教，不堆砌感叹号。直接输出回复内容。"
)

COMFORT_PROMPT = (  # negative → 排解
    "用户现在情绪低落。请先共情（接住情绪），再温和疏导：帮对方把感受正常化，"
    "给出 1~2 条可落地的小建议。避免「想开点」这类空话，不说教，不制造二次伤害。"
    "直接输出回复内容。"
)

NEUTRAL_PROMPT = (  # neutral → 自然接话
    "用户情绪平稳。请自然地接住话题，简短回应并适度关心，"
    "不要强行鼓励或安慰。直接输出回复内容。"
)

# ---- 条件路由表：label → branch（进程1 结果决定进程2 走哪条分支） ----
LABEL_TO_BRANCH = {
    "positive": "encourage",
    "negative": "comfort",
    "neutral": "neutral_chat",
}

# ---- branch → 对应提示词（进程2 使用） ----
BRANCH_PROMPTS = {
    "encourage": ENCOURAGE_PROMPT,
    "comfort": COMFORT_PROMPT,
    "neutral_chat": NEUTRAL_PROMPT,
}
