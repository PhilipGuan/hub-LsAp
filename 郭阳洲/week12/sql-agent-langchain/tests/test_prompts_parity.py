"""提示词平移等价性校验：源项目 string.Template vs 新版 ChatPromptTemplate。

渲染结果必须逐字节一致，证明 f-string 转义没有改变提示词语义。
源项目路径不存在时跳过（本测试面向平移验证场景）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from sql_agent.prompts import (
    GENERATOR_PROMPT,
    INTENT_PROMPT,
    REWRITE_PROMPT,
    SELECTOR_PROMPT,
    SUMMARY_PROMPT,
)

SOURCE_PROMPTS = Path(
    r"D:\BaiduNetdiskDownload\ai\直播课程\十一周\第11周-ChatBI数据智能问答"
    r"\Week12\06_SQL-Agent-Project\sql_agent\prompts.py"
)

DUMMY = {
    "catalog": "CATALOG_TEXT",
    "question": "QUESTION_TEXT",
    "schema": "SCHEMA_TEXT",
    "semantic": "SEMANTIC_TEXT",
    "few_shots": "FEW_SHOTS_TEXT",
    "data_until": "2013-12-22",
    "sql": "SELECT 1",
    "error": "E5002 unknown table",
    "history": "无",
    "row_count": "3",
    "truncated_note": "（已截断）",
    "metric": "销售额（公式 SUM）",
    "time_range": "2009 年",
    "assumptions": "无",
    "result_preview": "| a | b |\n| --- | --- |\n| 1 | 2 |",
}

PARITY_CASES = {
    "SELECTOR_PROMPT": (
        ["catalog", "question"],
        SELECTOR_PROMPT,
    ),
    "GENERATOR_PROMPT": (
        ["schema", "semantic", "few_shots", "question", "data_until"],
        GENERATOR_PROMPT,
    ),
    "REWRITE_PROMPT": (
        ["schema", "semantic", "question", "sql", "error", "history", "data_until"],
        REWRITE_PROMPT,
    ),
    "SUMMARY_PROMPT": (
        [
            "question",
            "sql",
            "row_count",
            "truncated_note",
            "metric",
            "time_range",
            "data_until",
            "assumptions",
            "result_preview",
        ],
        SUMMARY_PROMPT,
    ),
}


def _load_source_prompts():
    if not SOURCE_PROMPTS.exists():
        pytest.skip(f"源项目 prompts.py 不存在: {SOURCE_PROMPTS}")
    spec = importlib.util.spec_from_file_location("source_prompts", SOURCE_PROMPTS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", sorted(PARITY_CASES))
def test_prompt_parity_with_source(name: str) -> None:
    source = _load_source_prompts()
    variables, new_template = PARITY_CASES[name]
    old = getattr(source, name)
    values = {key: DUMMY[key] for key in variables}

    expected = old.substitute(**values)
    rendered = new_template.format_messages(**values)[0].content

    assert rendered == expected


def test_intent_prompt_renders_json_example() -> None:
    """INTENT_PROMPT 为新增集中管理模板：校验花括号转义正确、变量正确替换。"""
    rendered = INTENT_PROMPT.format_messages(question="销售额怎么样")[0].content

    assert (
        '{"intent":"analytics|meta|unsupported|out_of_scope|chitchat|ambiguous"'
        in rendered
    )
    assert '问题：销售额怎么样' in rendered
    assert "{question}" not in rendered
