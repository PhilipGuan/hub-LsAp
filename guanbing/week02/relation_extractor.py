import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

FILE_DIR = Path(__file__).resolve().parent
if str(FILE_DIR) not in sys.path:
    sys.path.insert(0, str(FILE_DIR))

from api_llm import chat, load_env  # noqa: E402

FALLBACK_CORPUS = [
    "小明喜欢小姚，但是小姚喜欢小王。",
    "张老师很欣赏学生小李，经常推荐她参加学科竞赛。",
    "王经理是李工的直接领导，李工一向尊重王经理的决定。",
    "老陈和老周是二十多年的老邻居，私下也是最好的朋友。",
    "小红非常讨厌室友小林，因为对方经常深夜听歌而不戴耳机。",
]

_DEFAULT_PROVIDER = "deepseek"

_EXTRACT_SYSTEM_PROMPT = """You are a precise information extraction agent for Chinese human-relation sentences.
Task: extract the PRIMARY relation triple(s) from the user sentence and output ONLY a valid JSON array.

Rules:
1) Output must be a JSON array at the root level. Example:
   [{"source": "小明", "relation": "admires", "target": "小姚"}]
2) Each element has exactly three string keys: source, relation, target.
3) relation label MUST be in English (R2 rule). Use labels like: admires, likes, dislikes, hates, respects, friend_of, colleague_of, reports_to, teacher_of, parent_of, child_of, spouse_of, neighbor_of, mentor_of, classmate_of, roommates_with, appreciates, envies, cherishes, etc.
4) When a sentence contains multiple relation facts, you MUST follow Rule A:
   - keep ONLY the primary relation that shows an unrequited love / emotional contrast / emotional gap pattern.
   - drop other plain relations even if they are factually present.
   Example input: 小明喜欢小姚，但是小姚喜欢小王。
   Expected output: [{"source":"小明","relation":"admires","target":"小姚"}]
5) Do NOT wrap output in markdown. No explanation text. No JSON object wrapper.
6) If you can not decide, return an empty array [] instead of guessing.
"""

_EXTRACT_USER_TEMPLATE = """请严格依据规则抽取下面句子的人物关系，输出JSON数组。
句子：{text}
"""

_EXTRACT_RETRY_USER_TEMPLATE = """请只输出根结构为JSON数组的结果，不要输出任何解释文本或代码块。
句子：{text}
"""

_CORPUS_SYSTEM_TEMPLATE = """You are a corpus generator. Generate exactly {n} short Chinese sentences (1-2 clauses each) about human relationships.
Output MUST be a strict JSON object with exactly one key:
  {{"corpus": ["句子1", "句子2", ...]}}

Corpus constraints:
1) Exactly {n} sentences.
2) Sentences must be natural Chinese and contain at least 2 named characters per sentence.
3) Cover at least 4 of these relation categories: family, workplace, social/friendship, positive sentiment, negative sentiment, authority/mentor.
4) EXACTLY 1 sentence should contain an unrequited love / emotional contrast pattern (a likes b, but b likes c; or a admires b, b ignores a). This reserved slot is for testing Rule A.
5) Placeholder names should be simple Chinese names like 小明, 小姚, 小王, 老李, 张老师, 李经理, 小红, 小林, 老陈, 老周.
6) No extra explanation. No markdown wrapper.
"""

_CORPUS_USER_TEMPLATE = """请生成 {n} 条人物关系短句子，遵守约束。"""

_CORPUS_RETRY_USER_TEMPLATE = """请只输出 {"corpus": [...]} 格式的JSON对象，不要加解释文字或代码块。"""


def load_env_if_needed() -> Optional[Path]:
    return load_env()


def _strip_code_fences(raw: str) -> str:
    if raw is None:
        return ""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_\-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_json_array(raw: str) -> Any:
    text = _strip_code_fences(raw)
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except Exception:
        pass

    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        candidate = text[start : end + 1]
        try:
            data = json.loads(candidate)
            if isinstance(data, list):
                return data
        except Exception:
            pass
    raise ValueError("Failed to extract JSON array from model output")


def _extract_json_object(raw: str) -> Any:
    text = _strip_code_fences(raw)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start : end + 1]
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    raise ValueError("Failed to extract JSON object from model output")


_RELATION_HINT = re.compile(r"^[A-Za-z_]+$")


def _normalize_graph(raw_graph: Any) -> list[dict]:
    if not isinstance(raw_graph, list):
        return []

    normalized: list[dict] = []
    for item in raw_graph:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source", "")).strip()
        relation = str(item.get("relation", "")).strip()
        target = str(item.get("target", "")).strip()
        if not source or not relation or not target:
            continue
        if not _RELATION_HINT.match(relation):
            print(f"[WARN] relation label may not be English R2-style: {relation!r}")
        normalized.append(
            {
                "source": source,
                "relation": relation,
                "target": target,
            }
        )
    return normalized


def _run_with_retries(fn, retries: int):
    last_error: Optional[Exception] = None
    for attempt in range(max(1, retries + 1)):
        try:
            return fn(attempt), None
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt >= retries:
                break
    return None, last_error


def generate_relation_corpus(
    n: int = 5,
    provider: str = _DEFAULT_PROVIDER,
    model_override: Optional[str] = None,
    max_retries: int = 2,
) -> list[str]:
    load_env_if_needed()

    system_prompt = _CORPUS_SYSTEM_TEMPLATE.format(n=n)
    user_prompt = _CORPUS_USER_TEMPLATE.format(n=n)

    def _attempt(attempt_index: int) -> list[str]:
        current_user = user_prompt if attempt_index == 0 else _CORPUS_RETRY_USER_TEMPLATE
        raw = chat(
            provider=provider,
            system_prompt=system_prompt,
            prompt=current_user,
            model_override=model_override,
            json_mode=True,
        )
        obj = _extract_json_object(raw)
        corpus = obj.get("corpus")
        if not isinstance(corpus, list):
            raise ValueError("Model JSON does not contain corpus list")
        sentences = [str(item).strip() for item in corpus if isinstance(item, str) and str(item).strip()]
        if len(sentences) < n:
            raise ValueError(f"Model produced only {len(sentences)} sentences, expected {n}")
        return sentences[:n]

    corpus, err = _run_with_retries(_attempt, retries=max_retries)
    if err is not None or corpus is None:
        print(f"[WARN] generate_relation_corpus failed after retries, using fallback corpus: {err}")
        return list(FALLBACK_CORPUS)
    return corpus


def extract_relation_graph(
    text: str,
    provider: str = _DEFAULT_PROVIDER,
    model_override: Optional[str] = None,
    max_retries: int = 2,
) -> list[dict]:
    load_env_if_needed()

    user_prompt = _EXTRACT_USER_TEMPLATE.format(text=text)

    def _attempt(attempt_index: int) -> list[dict]:
        current_user = user_prompt if attempt_index == 0 else _EXTRACT_RETRY_USER_TEMPLATE.format(text=text)
        raw = chat(
            provider=provider,
            system_prompt=_EXTRACT_SYSTEM_PROMPT,
            prompt=current_user,
            model_override=model_override,
            json_mode=True,
        )
        graph = _extract_json_array(raw)
        return _normalize_graph(graph)

    graph, err = _run_with_retries(_attempt, retries=max_retries)
    if err is not None or graph is None:
        print(f"[WARN] extract_relation_graph failed after retries, returning empty graph: {err}")
        return []
    return graph


def run_corpus(
    corpus: list[str],
    provider: str = _DEFAULT_PROVIDER,
    model_override: Optional[str] = None,
) -> list[dict]:
    report: list[dict] = []
    for idx, text in enumerate(corpus, start=1):
        entry = {
            "index": idx,
            "text": text,
            "graph": [],
            "ok": False,
            "error": None,
        }
        try:
            graph = extract_relation_graph(text, provider=provider, model_override=model_override)
            entry["graph"] = graph
            entry["ok"] = True
        except Exception as exc:  # noqa: BLE001
            entry["error"] = f"{type(exc).__name__}: {exc}"
            entry["ok"] = False
        report.append(entry)
    return report


def print_report(report: list[dict]) -> None:
    print("\n" + "=" * 60)
    print("RELATION EXTRACTION HOMEWORK REPORT")
    print("=" * 60)
    for entry in report:
        print("\n" + "-" * 60)
        print(f"语料 {entry['index']} / {len(report)}")
        print(f"输入：{entry['text']}")
        if entry.get("error"):
            print(f"错误：{entry['error']}")
        print("输出：")
        print(json.dumps(entry["graph"], ensure_ascii=False, indent=2))
        print(f"OK：{entry['ok']}")
    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60 + "\n")
