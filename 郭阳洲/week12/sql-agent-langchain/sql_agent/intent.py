from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .budget import Budget, BudgetExceeded
from .errors import AgentError
from .llm import LLM, LLMError
from .prompts import INTENT_PROMPT
from .semantic import SemanticCatalog

ANALYTICS = "analytics"
META = "meta"
UNSUPPORTED = "unsupported"
OUT_OF_SCOPE = "out_of_scope"
CHITCHAT = "chitchat"
AMBIGUOUS = "ambiguous"

_WRITE = re.compile(
    r"(删除|删掉|清空|写入|插入|新增|修改|更改|更新|建表|创建表|"
    r"drop\s|delete\s|update\s|insert\s|alter\s|truncate\s)",
    re.I,
)
_CAUSAL = re.compile(r"(为什么|为何|原因是什么|导致|归因|影响因素|是因为)")
_PREDICTION = re.compile(
    r"(预测|预估|预计|未来|明年|下个月|下季度|将会|forecast|趋势会)", re.I
)
_PLANNING = re.compile(r"(如何提升|怎么提升|优化建议|策略建议|营销方案|解决方案|行动计划)")
_OUT_OF_SCOPE = re.compile(
    r"(股票|基金|天气|汇率|工资|薪资|库存|物流|竞品|市场调研|用户画像|情感分析)"
)
_META = re.compile(
    r"(有哪些表|哪些字段|数据库结构|表结构|字段说明|能问什么|可以问什么|"
    r"怎么用|使用说明|数据范围|数据截止|多少张表|有哪些数据|支持哪些问题)"
)
_CHITCHAT = re.compile(
    r"^(你好|您好|hi|hello|嗨|在吗|谢谢|再见|你是谁|介绍一下你)[!！。？?\s]*$", re.I
)
_AMBIGUOUS = re.compile(
    r"(表现怎么样|表现如何|情况怎么样|情况如何|数据怎么样|帮我看看|分析一下|"
    r"看下(销售|数据|情况)|销售怎么样|业务怎么样)"
)
_METRIC_CUES = re.compile(
    r"(销售额|收入|营收|销量|客户|用户|顾客|歌曲|曲目|专辑|艺人|流派|发票|金额|时长|数量)"
)

_MESSAGES = {
    "E1003": "我是 Chinook 数据问答助手，可以回答销售额、销量、客户、歌曲等问题。",
    "E1004": "我只能做只读查询，不能修改数据；当前数据只覆盖 Chinook 音乐商店的客户、发票、曲目等。",
    "E1005": "请补充指标或时间范围。例如：“2009 年每个国家的销售额是多少？”",
    "E1007": "本期不做因果或预测分析。可以改为查看历史趋势或对比，例如：“2009 年每个月的销售额是多少？”",
}
_SUGGESTIONS = {
    "E1003": ["数据库里有哪些表？", "2009 年每个月的销售额是多少？"],
    "E1004": ["销售额最高的前 5 个国家是哪些？", "一共有多少位客户？"],
    "E1005": ["2009 年每个月的销售额是多少？", "每个国家的客户数是多少？"],
    "E1007": ["2009 年每个月的销售额是多少？", "每个流派的销量是多少？"],
}


@dataclass
class IntentResult:
    intent: str
    code: str = ""
    message: str = ""
    confidence: str = "high"
    matched: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    @property
    def is_analytics(self) -> bool:
        return self.intent == ANALYTICS

    @property
    def refused(self) -> bool:
        return self.intent != ANALYTICS

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "code": self.code,
            "message": self.message,
            "confidence": self.confidence,
            "matched": list(self.matched),
            "suggestions": list(self.suggestions),
        }


class IntentGate:
    """规则优先的意图门禁；高风险类别不进入选表与生成。"""

    def __init__(self, semantic: SemanticCatalog | None = None):
        self.semantic = semantic

    def rule_classify(self, question: str) -> IntentResult:
        text = (question or "").strip()
        matched = self._match(text)

        if _WRITE.search(text):
            return self._refuse(OUT_OF_SCOPE, "E1004", matched)
        if _CAUSAL.search(text) or _PREDICTION.search(text) or _PLANNING.search(text):
            return self._refuse(UNSUPPORTED, "E1007", matched)
        if _OUT_OF_SCOPE.search(text) and not self._has_metric(text):
            return self._refuse(OUT_OF_SCOPE, "E1004", matched)
        if _META.search(text):
            return self._refuse(META, "E1003", matched)
        if _CHITCHAT.match(text) and not self._has_metric(text):
            return self._refuse(CHITCHAT, "E1003", matched)
        if _AMBIGUOUS.search(text) and not self._has_metric(text):
            return self._refuse(AMBIGUOUS, "E1005", matched)
        return IntentResult(ANALYTICS, confidence="high", matched=matched)

    def classify(
        self,
        question: str,
        llm: LLM | None = None,
        budget: Budget | None = None,
        use_llm: bool = False,
    ) -> IntentResult:
        rule = self.rule_classify(question)
        if not rule.is_analytics:
            return rule
        if not use_llm or llm is None or budget is None or not budget.can_call():
            return rule
        if not self._needs_llm(question):
            return rule

        messages = INTENT_PROMPT.format_messages(question=question)
        try:
            data = llm.chat_json(
                messages,
                temperature=0.0,
                max_tokens=160,
                budget=budget,
                step="intent",
            )
        except (LLMError, BudgetExceeded, AgentError):
            # 高风险问题在模型不可用时失败关闭，不盲目生成 SQL
            if self._needs_llm(question):
                return self._refuse(AMBIGUOUS, "E1005", rule.matched, confidence="low")
            return rule

        intent = str(data.get("intent") or "").strip().lower()
        confidence = str(data.get("confidence") or "medium")
        if intent in {ANALYTICS, META, CHITCHAT, AMBIGUOUS}:
            code = {"meta": "E1003", "chitchat": "E1003", "ambiguous": "E1005"}.get(
                intent, ""
            )
            if intent == ANALYTICS:
                return IntentResult(ANALYTICS, confidence=confidence, matched=rule.matched)
            return self._refuse(intent, code, rule.matched, confidence=confidence)
        if intent in {UNSUPPORTED, OUT_OF_SCOPE}:
            code = "E1004" if intent == OUT_OF_SCOPE else "E1007"
            return self._refuse(intent, code, rule.matched, confidence=confidence)
        return self._refuse(AMBIGUOUS, "E1005", rule.matched, confidence="low")

    def _refuse(
        self,
        intent: str,
        code: str,
        matched: list[str],
        confidence: str = "high",
    ) -> IntentResult:
        return IntentResult(
            intent=intent,
            code=code,
            message=_MESSAGES.get(code, ""),
            confidence=confidence,
            matched=matched,
            suggestions=list(_SUGGESTIONS.get(code, [])),
        )

    def _has_metric(self, text: str) -> bool:
        if self.semantic is not None:
            if self.semantic.detect_metrics(text) or self.semantic.detect_undefined(text):
                return True
        return bool(_METRIC_CUES.search(text))

    def _match(self, text: str) -> list[str]:
        patterns = [
            ("write", _WRITE),
            ("causal", _CAUSAL),
            ("prediction", _PREDICTION),
            ("planning", _PLANNING),
            ("out_of_scope", _OUT_OF_SCOPE),
            ("meta", _META),
            ("chitchat", _CHITCHAT),
            ("ambiguous", _AMBIGUOUS),
        ]
        return [name for name, pattern in patterns if pattern.search(text)]

    @staticmethod
    def _needs_llm(question: str) -> bool:
        return bool(
            re.search(
                r"(分析|看看|表现|情况|怎么样|如何|帮忙|建议|有没有|是否|值得)", question
            )
        )


def meta_answer(catalog: Any, question: str = "") -> str:
    tables = ", ".join(catalog.table_names)
    data_until = getattr(catalog, "data_until", None) or "未知"
    lines = [
        f"当前可以查询 Chinook 音乐商店数据库，共 {len(catalog.table_names)} 张表：{tables}。",
        f"数据截止：{data_until}。",
        "可以问销售额、销量、客户数、歌曲数、人均消费、TopN、按月趋势、国家/流派对比等问题。",
        "例如：",
        "- 销售额最高的前 5 个国家是哪些？",
        "- 2009 年每个月的销售额是多少？",
        "- 每个流派有多少首歌曲？",
    ]
    if question:
        lines.append("（这是元问题，不生成 SQL。）")
    return "\n".join(lines)
