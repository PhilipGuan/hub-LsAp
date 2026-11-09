"""引用校验与修复 —— 本项目最核心的一块。

**不能信任 LLM 输出的引用。** 实测它会：引用一个根本没给出的编号、把编号写成
``[S1, S2]``、在毫无来源的句子上光秃秃地不写任何引用、以及自己随手加「（模型推断）」
来糊弄过去。所以这里的规则是：

> **程序判定优先于 LLM 自述。**

程序做四件事：

1. **扫** —— 正则找出所有 ``[Sn]``，与合法编号集合比对。
2. **修** —— 未知编号按占比决定「就地替换成（引用缺失）」还是「重写整段」。
3. **标** —— 逐句切分，**没有合法引用的句子由程序强制加 ``⚠️ 模型推断：`` 前缀**。
   即使 LLM 自己写了「（模型推断）」，程序也按有无引用重新判一遍。
4. **统** —— 输出统计，供置信度的 citation_coverage 因子与硬上限使用。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# [S3] / [S12]
_CITATION_RE = re.compile(r"\[S(\d+)\]")

# 无来源结论的显式标记。用 ⚠️ 是为了在 markdown 里足够扎眼，藏不住。
INFERRED_PREFIX = "⚠️ 模型推断："
# LLM 自己可能写的各种变体，用来在做「重判」时先把它们剥掉
_LLM_INFERRED_PATTERNS = (
    re.compile(r"^\s*[（(]\s*模型推断\s*[)）]\s*[:：]?\s*"),
    re.compile(r"^\s*模型推断\s*[:：]\s*"),
    re.compile(r"^\s*⚠️\s*模型推断\s*[:：]\s*"),
)

# 未知编号占比超过这个值时，认为「就地替换」已经救不回来了，触发一次重写
UNKNOWN_RATIO_REPAIR_THRESHOLD = 0.15
# 触发重写后仍然超标 —— 置信度要被打上这个硬上限
UNKNOWN_RATIO_HARD_CAP = 0.5

MISSING_CITATION_MARKER = "（引用缺失）"


def normalize_sid(value: str | int) -> str:
    """``3`` / ``"3"`` / ``"S3"`` 都归一成 ``"S3"``。"""
    text = str(value).strip().upper()
    if text.startswith("S"):
        text = text[1:]
    return f"S{text}"


def find_markers(text: str) -> list[str]:
    """按出现顺序找出全部引用编号（含重复）。"""
    return [normalize_sid(m) for m in _CITATION_RE.findall(text or "")]


@dataclass(slots=True)
class CitationStats:
    total_markers: int = 0
    valid_markers: int = 0
    unknown_markers: int = 0
    replaced_markers: int = 0
    inferred_sentences: int = 0
    sentences: int = 0
    repair_attempted: bool = False
    repair_succeeded: bool = False
    unknown_ratio: float = 0.0

    @property
    def citation_coverage(self) -> float:
        """有合法引用的句子占比 —— 置信度用。"""
        if self.sentences <= 0:
            return 0.0
        return (self.sentences - self.inferred_sentences) / self.sentences

    def as_dict(self) -> dict[str, object]:
        return {
            "total_markers": self.total_markers,
            "valid_markers": self.valid_markers,
            "unknown_markers": self.unknown_markers,
            "replaced_markers": self.replaced_markers,
            "inferred_sentences": self.inferred_sentences,
            "sentences": self.sentences,
            "unknown_ratio": round(self.unknown_ratio, 4),
            "repair_attempted": self.repair_attempted,
            "repair_succeeded": self.repair_succeeded,
        }


def replace_unknown_markers(text: str, valid_sids: set[str]) -> tuple[str, int]:
    """把未知编号就地替换成 ``（引用缺失）``，返回 (新文本, 替换数)。

    保留合法编号 —— 只清掉对不上的那些。
    """
    replaced = 0

    def _sub(match: re.Match[str]) -> str:
        nonlocal replaced
        sid = normalize_sid(match.group(1))
        if sid in valid_sids:
            return match.group(0)
        replaced += 1
        return MISSING_CITATION_MARKER

    return _CITATION_RE.sub(_sub, text), replaced


def strip_llm_inferred_prefix(sentence: str) -> str:
    """剥掉 LLM 自己加的「（模型推断）」前缀 —— 之后由程序统一重判。"""
    for pattern in _LLM_INFERRED_PATTERNS:
        stripped = pattern.sub("", sentence, count=1)
        if stripped != sentence:
            return stripped.strip()
    return sentence


# 中文句末标点 + 换行。保留标点本身，拼回去时才不会丢句子边界。
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？；!?;])\s*|\n+")

# 这些行不参与逐句标注：标题、表格、代码围栏、纯列表符号
_SKIP_LINE_RE = re.compile(r"^\s*(#{1,6}\s|\||```|~~~|>)")
_BULLET_RE = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+)")


def split_sentences(text: str) -> list[str]:
    """把一段 markdown 切成句子（保留标点）。"""
    if not text:
        return []
    return [part for part in _SENTENCE_SPLIT_RE.split(text) if part and part.strip()]


def mark_inferred(text: str, valid_sids: set[str]) -> tuple[str, int]:
    """给没有合法引用的句子加 ``⚠️ 模型推断：`` 前缀。返回 (新文本, 标记数)。

    **幂等**：已经有前缀的句子不会被再加一次。

    只处理正文句子 —— 标题、表格、代码块、引用块会原样跳过，往那些地方插前缀
    只会把 markdown 结构弄坏。
    """
    if not text:
        return text, 0

    marked = 0
    out_lines: list[str] = []
    in_fence = False

    for line in text.split("\n"):
        if line.strip().startswith(("```", "~~~")):
            in_fence = not in_fence
            out_lines.append(line)
            continue

        if in_fence or not line.strip() or _SKIP_LINE_RE.match(line):
            out_lines.append(line)
            continue

        bullet = _BULLET_RE.match(line)
        prefix, body = (bullet.group(1), line[bullet.end() :]) if bullet else ("", line)

        rebuilt: list[str] = []
        for sentence in split_sentences(body):
            # 已经是本函数标的 → 原样放行。这一步必须在剥离之前做，
            # 否则会先剥掉自己的标记、再重新标上，计数器平白多算一次。
            if sentence.startswith(INFERRED_PREFIX):
                rebuilt.append(sentence)
                continue

            clean = strip_llm_inferred_prefix(sentence)
            has_citation = any(sid in valid_sids for sid in find_markers(clean))
            if has_citation or not clean.strip():
                rebuilt.append(clean)
            else:
                rebuilt.append(f"{INFERRED_PREFIX}{clean}")
                marked += 1

        out_lines.append(prefix + "".join(rebuilt) if rebuilt else line)

    return "\n".join(out_lines), marked


def check_text(text: str, valid_sids: set[str]) -> CitationStats:
    """只做统计，不改文本。"""
    markers = find_markers(text)
    unknown = [m for m in markers if m not in valid_sids]

    stats = CitationStats(
        total_markers=len(markers),
        valid_markers=len(markers) - len(unknown),
        unknown_markers=len(unknown),
    )
    stats.unknown_ratio = len(unknown) / len(markers) if markers else 0.0
    return stats


def process_text(
    text: str,
    valid_sids: set[str],
    *,
    repair_threshold: float = UNKNOWN_RATIO_REPAIR_THRESHOLD,
) -> tuple[str, CitationStats]:
    """完整走一遍：替换未知编号 → 标注无来源句子 → 出统计。

    注意 ``unknown_ratio == 0`` 时是**零成本快路径**，绝大多数段落都会走这条，
    不用为每次调用都付出逐句切分的代价。
    """
    stats = check_text(text, valid_sids)

    if stats.unknown_markers == 0:
        marked_text, inferred = mark_inferred(text, valid_sids)
        stats.inferred_sentences = inferred
        stats.sentences = len([s for s in split_sentences(text) if s.strip()])
        return marked_text, stats

    if stats.unknown_ratio > repair_threshold:
        # 超标太多 —— 调用方应该拿这个统计去触发一次重写，这里先把编号清干净
        logger.warning(
            "未知引用占比 %.1f%% 超过 %.0f%%，建议触发重写",
            stats.unknown_ratio * 100,
            repair_threshold * 100,
        )

    cleaned, replaced = replace_unknown_markers(text, valid_sids)
    stats.replaced_markers = replaced

    marked_text, inferred = mark_inferred(cleaned, valid_sids)
    stats.inferred_sentences = inferred
    stats.sentences = len([s for s in split_sentences(cleaned) if s.strip()])
    return marked_text, stats


def merge_stats(stats_list: list[CitationStats]) -> CitationStats:
    """把多个段落的统计合成一份 —— 报告级的整体引用健康度。"""
    merged = CitationStats()
    for stats in stats_list:
        merged.total_markers += stats.total_markers
        merged.valid_markers += stats.valid_markers
        merged.unknown_markers += stats.unknown_markers
        merged.replaced_markers += stats.replaced_markers
        merged.inferred_sentences += stats.inferred_sentences
        merged.sentences += stats.sentences
        merged.repair_attempted = merged.repair_attempted or stats.repair_attempted
        merged.repair_succeeded = merged.repair_succeeded or stats.repair_succeeded

    if merged.total_markers:
        merged.unknown_ratio = merged.unknown_markers / merged.total_markers
    return merged


__all__ = [
    "INFERRED_PREFIX",
    "MISSING_CITATION_MARKER",
    "UNKNOWN_RATIO_HARD_CAP",
    "UNKNOWN_RATIO_REPAIR_THRESHOLD",
    "CitationStats",
    "check_text",
    "find_markers",
    "mark_inferred",
    "merge_stats",
    "normalize_sid",
    "process_text",
    "replace_unknown_markers",
    "split_sentences",
    "strip_llm_inferred_prefix",
]
