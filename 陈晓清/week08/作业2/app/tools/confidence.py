"""置信度计算——**纯函数，不吃 LLM**，因此和 `search` 一样住在 tools/ 而不是 Agent 层。

当前口径只看**真实来源数量**（用户定）：域名多样性 / 权威来源 / 交叉印证三项暂不参与，
所以一个分节的得分只由它实际引用到几条来源决定。好处是产物 JSON 里现成的数据就能复算
（验收项 7），不需要把检索过程也存下来。

输入是已经收敛好的 `Section` / `Conclusion`——编号在 `SummaryAgent` / `ReportAgent` 那一步
已经过闸，这里不再做一遍越界校验。
"""

from datetime import datetime

from app.config import (
    CONFIDENCE_BASE,
    CONFIDENCE_LEVEL_HIGH,
    CONFIDENCE_LEVEL_MEDIUM,
    CONFIDENCE_PER_SOURCE,
    CONFIDENCE_ROUNDING,
    CONFIDENCE_SOURCE_CAP,
)
from app.models import Conclusion, Confidence, Section, SectionConfidence


def _score(cited: int) -> float:
    """一个分节的得分：基线 + 每条来源一档，封顶 `CONFIDENCE_SOURCE_CAP` 条。

    一条来源都没有的分节**不给基线分**——0 分是在说"这一节没有任何检索结果支撑"，
    给 0.30 会让它看上去比"有 1 条来源"只差一点点，那是假的可信度。
    """
    if cited <= 0:
        return 0.0
    # 分节得分同样落到 0.05 的整数倍：整体分是它们的加权平均，只要这里干净，
    # 读产物的人拿 per_section 就能把 overall 复算回去（验收项 7）。
    return _round(CONFIDENCE_BASE + min(cited, CONFIDENCE_SOURCE_CAP) * CONFIDENCE_PER_SOURCE)


def _round(score: float) -> float:
    """四舍五入到 0.05。顺带把浮点尾巴（0.7000000000000001）压回两位小数。"""
    return round(round(score / CONFIDENCE_ROUNDING) * CONFIDENCE_ROUNDING, 2)


def _level(score: float) -> str:
    if score >= CONFIDENCE_LEVEL_HIGH:
        return "高"
    return "中" if score >= CONFIDENCE_LEVEL_MEDIUM else "低"


def compute_confidence(
    sections: list[Section], conclusions: list[Conclusion], now: datetime | None = None
) -> Confidence:
    """整体置信度 = 各分节**按来源数量加权**平均（决策 10）。

    权重就是分节自己的来源数，所以没有来源的分节权重为 0、自然不参与整体分——它的问题由
    `unsourced_claims` 和分节的 0.00 分开陈述，不混进整体分里。全部无来源时整体记 0.00。

    `now` 只是为了自测可控；不传就用当前时刻。信息截止时间取**检索时刻**：`Source` 没带
    发布时间，而"我的信息更新到什么时候"本来就是检索这件事的语义。
    """
    per_section: list[SectionConfidence] = []
    counts: list[int] = []
    for section in sections:
        cited = len(set(section.refs))  # 同一来源被引两次算一条
        counts.append(cited)
        per_section.append(SectionConfidence(sub_question=section.sub_question, score=_score(cited)))

    total = sum(counts)
    overall = sum(sc.score * n for sc, n in zip(per_section, counts)) / total if total else 0.0
    overall = _round(overall)

    return Confidence(
        overall=overall,
        level=_level(overall),
        info_cutoff=(now or datetime.now()).strftime("%Y-%m-%d"),
        unsourced_claims=sum(1 for c in conclusions if not c.refs),
        per_section=per_section,
    )


if __name__ == "__main__":
    # 纯本地逻辑（无网络），按约定用假数据自检并打印结果
    import sys

    sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 cp936，中文输出会乱码

    sections = [
        Section(sub_question="有 3 条来源", text="…", refs=[1, 2, 3]),
        Section(sub_question="只有 1 条来源", text="…", refs=[4]),
        Section(sub_question="重复引用同一个来源", text="…", refs=[4, 4, 4]),
        Section(sub_question="没检索到", text="…", refs=[]),
    ]
    conclusions = [Conclusion(text="有据可依", refs=[1]), Conclusion(text="纯模型推断", refs=[])]

    got = compute_confidence(sections, conclusions, now=datetime(2026, 9, 10))
    print("分节得分:", [(s.sub_question, s.score) for s in got.per_section])
    print("整体:", got.overall, got.level, "| 截止:", got.info_cutoff, "| 无来源结论:", got.unsourced_claims)

    # 1 条=0.50 / 2 条=0.70 / 3 条及以上=0.90（封顶）；0 条不给基线分
    assert [s.score for s in got.per_section] == [0.90, 0.50, 0.50, 0.00], got.per_section
    # 加权平均只算有来源的分节：(0.9×3 + 0.5×1 + 0.5×1) / 5 = 0.74 → 四舍五入 0.75
    assert got.overall == 0.75 and got.level == "高", got
    assert got.unsourced_claims == 1 and got.info_cutoff == "2026-09-10"

    empty = compute_confidence([Section(sub_question="无", text="…")], [], now=datetime(2026, 9, 10))
    assert empty.overall == 0.0 and empty.level == "低", empty  # 全无来源不除零
    print("confidence.py 自检 ok：来源数量分档 / 加权平均 / 0 条不给基线分 / 不除零")
