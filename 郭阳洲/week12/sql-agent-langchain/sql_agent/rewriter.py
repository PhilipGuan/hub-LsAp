from __future__ import annotations

import re

from .budget import Budget
from .generator import SQLGenerationError, extract_sql
from .llm import LLM, LLMError
from .prompts import REWRITE_PROMPT


class RewriteNoopError(RuntimeError):
    def __init__(self, message: str = "修复未产生新查询"):
        self.code = "E6010"
        super().__init__(message)


def normalize_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip().rstrip(";")).lower()


class SQLRewriter:
    def __init__(self, llm: LLM):
        self.llm = llm

    def rewrite(
        self,
        question: str,
        schema: str,
        sql: str,
        error: str,
        history: list[dict] | None = None,
        semantic: object | None = None,
        budget: Budget | None = None,
        data_until: str = "",
    ) -> str:
        history_text = "无"
        if history:
            history_text = "\n".join(
                f"- 第 {item['attempt']} 次 SQL: {item['sql']}\n  错误: {item['error']}"
                for item in history[-3:]
            )
        semantic_block = (
            semantic.prompt_block() if hasattr(semantic, "prompt_block") else "无"
        )
        messages = REWRITE_PROMPT.format_messages(
            schema=schema,
            semantic=semantic_block,
            question=question,
            sql=sql,
            error=error,
            history=history_text,
            data_until=data_until or "2013-12-22",
        )
        try:
            text = self.llm.chat(
                messages,
                temperature=0.1,
                max_tokens=1500,
                budget=budget,
                step="rewriter",
            )
        except LLMError as exc:
            raise SQLGenerationError(str(exc), exc.code) from exc
        fixed = extract_sql(text)
        if normalize_sql(fixed) == normalize_sql(sql):
            raise RewriteNoopError("修复后的 SQL 与原 SQL 相同，已停止改写")
        return fixed
