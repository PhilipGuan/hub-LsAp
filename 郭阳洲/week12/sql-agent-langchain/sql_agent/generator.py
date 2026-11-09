from __future__ import annotations

import re

from .budget import Budget
from .llm import LLM, LLMError
from .prompts import FEW_SHOTS, GENERATOR_PROMPT


class SQLGenerationError(RuntimeError):
    def __init__(self, message: str, code: str = "E4002"):
        self.code = code
        super().__init__(f"{code}: {message}")


def extract_sql(text: str) -> str:
    fenced = re.findall(r"```(?:sql)?\s*(.*?)```", text, re.S | re.I)
    if fenced:
        sql = fenced[0].strip()
    else:
        upper = text.upper()
        if "CANNOT_ANSWER" in upper:
            raise SQLGenerationError("模型认为当前数据无法回答该问题", "E4003")
        lines = [line.strip() for line in text.strip().splitlines()]
        start = next(
            (i for i, line in enumerate(lines) if re.match(r"^(select|with)\b", line, re.I)),
            None,
        )
        if start is None:
            raise SQLGenerationError(f"模型回答中没有 SQL: {text[:200]}", "E4002")
        sql = "\n".join(lines[start:])

    sql = sql.strip()
    if "CANNOT_ANSWER" in sql.upper():
        raise SQLGenerationError("模型认为当前数据无法回答该问题", "E4003")
    if not sql:
        raise SQLGenerationError("模型返回了空 SQL", "E4002")
    return sql


class SQLGenerator:
    def __init__(self, llm: LLM):
        self.llm = llm

    def generate(
        self,
        question: str,
        schema: str,
        semantic: object | None = None,
        budget: Budget | None = None,
        data_until: str = "",
    ) -> str:
        semantic_block = (
            semantic.prompt_block() if hasattr(semantic, "prompt_block") else "无"
        )
        messages = GENERATOR_PROMPT.format_messages(
            schema=schema,
            semantic=semantic_block,
            few_shots=FEW_SHOTS,
            question=question,
            data_until=data_until or "2013-12-22",
        )
        try:
            text = self.llm.chat(
                messages,
                temperature=0.1,
                max_tokens=1500,
                budget=budget,
                step="generator",
            )
        except LLMError as exc:
            raise SQLGenerationError(str(exc), exc.code) from exc
        return extract_sql(text)
