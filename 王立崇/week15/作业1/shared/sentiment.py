"""离线兜底：中文情绪词典 + 模板回复。

没有配置 LLM API Key（或调用失败）时，进程1 / 进程2 会退回到这里的实现，
保证整条流水线在没有网络、没有密钥的情况下依旧可以完整演示。
"""
from __future__ import annotations

POSITIVE_WORDS = (
    "开心", "高兴", "快乐", "顺利", "成功", "升职", "加薪", "表扬", "夸奖", "喜欢",
    "太棒", "真棒", "感谢", "谢谢", "满足", "满意", "期待", "幸运", "轻松", "放松",
    "幸福", "进步", "赢了", "通过", "录取", "offer", "excited", "happy", "great",
    "awesome", "love", "proud", "grateful", "thanks",
)

NEGATIVE_WORDS = (
    "难过", "伤心", "压力", "焦虑", "心烦", "崩溃", "好累", "疲惫", "失败", "失望",
    "孤独", "害怕", "生气", "愤怒", "讨厌", "担心", "失眠", "郁闷", "委屈", "内耗",
    "无助", "绝望", "痛苦", "吵架", "分手", "裁员", "加班", "sad", "angry", "anxious",
    "tired", "stress", "hate", "worried", "upset", "lonely", "exhausted", "depressed",
)


def rule_sentiment(text: str) -> tuple[str, float, str]:
    """返回 (sentiment, confidence, reason)。"""
    lowered = text.lower()
    positive_hits = [word for word in POSITIVE_WORDS if word in text or word in lowered]
    negative_hits = [word for word in NEGATIVE_WORDS if word in text or word in lowered]

    pos, neg = len(positive_hits), len(negative_hits)
    if pos > neg:
        confidence = min(0.9, 0.55 + 0.1 * pos)
        return "positive", confidence, f"命中正向词：{'、'.join(positive_hits[:3])}"
    if neg > pos:
        confidence = min(0.9, 0.55 + 0.1 * neg)
        return "negative", confidence, f"命中负向词：{'、'.join(negative_hits[:3])}"
    if pos and neg:
        return "neutral", 0.5, "正负情绪词数量相当"
    return "neutral", 0.5, "未命中明显情绪词"


_TEMPLATES: dict[str, tuple[str, ...]] = {
    "encourage": (
        "太棒了，替你开心！这份好心情是你自己挣来的，记得好好享受它。"
        "要不要把它记下来，等哪天需要打气时再翻出来看看？继续冲，我在这儿给你加油！",
        "听到这个消息我也跟着高兴！你在这件事上付出的认真终于有了回音。"
        "趁状态正好，给自己一个小奖励吧 —— 哪怕只是一杯喜欢的饮料。",
    ),
    "comfort": (
        "听起来你现在心里挺沉的，先停一下也没关系。情绪不是软弱，它在提醒你：有些东西需要被照顾。"
        "可以先深呼吸几次，然后只挑一件 5 分钟内能做完的小事，让自己重新找到一点掌控感。你不是一个人在扛。",
        "抱抱你。难受的时候不必急着振作，允许自己先低落一会儿。"
        "如果可以，把此刻最困扰你的那一点写下来，写出来往往就没那么压人了。需要的话，随时跟我说说。",
    ),
    "neutral": (
        "我收到了你的想法。要不要再多说一点？比如发生了什么、你当时最在意的是什么，我可以陪你一起理一理。",
        "嗯，我在听。你可以放心地多说一些，我们一起看看有没有可以着手的地方。",
    ),
}


def fallback_reply(style: str, text: str) -> str:
    templates = _TEMPLATES.get(style, _TEMPLATES["neutral"])
    index = sum(ord(ch) for ch in text) % len(templates)
    return templates[index]
