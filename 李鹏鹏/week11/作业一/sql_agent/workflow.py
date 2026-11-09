"""SQL Agent 工作流编排。

流程：
    1. 数据库解析   DBParser 读取表结构、外键、字段分布
    2. table 选择   LLM 根据问题挑选最少的表
    3. SQL 生成     LLM 根据 schema 生成 SQLite 查询
    4. SQL 校验     只读检查 + EXPLAIN QUERY PLAN 编译
    5. SQL 改写     校验/执行失败时，带着报错让 LLM 修复
    6. SQL 执行     只读执行并限制行数、设置超时
    7. 结果汇总     LLM 把结构化结果转成自然语言

每一步都是独立、可观测的，避免把“生成 + 执行 + 总结”塞进一次
不可控的 Agent 调用。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from .config import Settings
from .db_parser import DBParser, DBParserError, normalize_sql, rows_to_markdown
from .llm import LLMClient, LLMError, LLMProtocol
from .prompts import (
    RESULT_SUMMARY_PROMPT,
    SQL_GENERATION_PROMPT,
    SQL_REPAIR_PROMPT,
    TABLE_SELECT_PROMPT,
    render,
)


class WorkflowError(RuntimeError):
    """工作流无法继续执行的错误。"""


# ---------------------------------------------------------------------- #
# 数据结构
# ---------------------------------------------------------------------- #
@dataclass
class StepTrace:
    name: str
    elapsed: float
    detail: str = ""


@dataclass
class SQLAgentResult:
    question: str
    tables: list[str] = field(default_factory=list)
    sql: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    truncated: bool = False
    answer: str = ""
    attempts: int = 0
    error: str = ""
    traces: list[StepTrace] = field(default_factory=list)
    llm_usage: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.answer)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "tables": self.tables,
            "sql": self.sql,
            "columns": self.columns,
            "rows": [list(r) for r in self.rows],
            "truncated": self.truncated,
            "answer": self.answer,
            "attempts": self.attempts,
            "error": self.error,
            "traces": [
                {"name": t.name, "elapsed": round(t.elapsed, 3), "detail": t.detail}
                for t in self.traces
            ],
            "llm_usage": self.llm_usage,
        }


# ---------------------------------------------------------------------- #
# 工具函数
# ---------------------------------------------------------------------- #
def extract_json(text: str) -> Optional[dict[str, Any]]:
    """从模型输出中尽量提取 JSON 对象。

    逐个 ``{`` 尝试 ``raw_decode``，可以容忍 JSON 前后夹带解释文字。
    """
    cleaned = text.strip()
    cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def extract_sql(text: str) -> str:
    """从模型输出中提取 SQL。"""
    if not text:
        return ""

    fenced = re.search(r"```(?:sql)?\s*(.*?)```", text, re.IGNORECASE | re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    candidate = normalize_sql(candidate)

    if re.match(r"^\s*(SELECT|WITH)\b", candidate, re.IGNORECASE):
        return candidate

    # 兜底：从自由文本中截取第一个 SELECT / WITH
    match = re.search(r"\b(SELECT|WITH)\b", text, re.IGNORECASE)
    if match:
        tail = normalize_sql(text[match.start() :])
        # 去掉 SQL 后面可能跟着的解释性 Markdown
        tail = re.split(r"\n\s*```", tail, maxsplit=1)[0]
        return normalize_sql(tail)

    return ""


# ---------------------------------------------------------------------- #
# 工作流
# ---------------------------------------------------------------------- #
class SQLAgentWorkflow:
    """把自然语言问题转换为 SQL，执行并总结结果。"""

    def __init__(
        self,
        db_path: str | Path,
        settings: Optional[Settings] = None,
        llm: Optional[LLMProtocol] = None,
        on_step: Optional[Callable[[StepTrace], None]] = None,
    ) -> None:
        self.settings = settings or Settings.load()
        self.parser = DBParser(db_path)
        self._llm = llm
        self.on_step = on_step

    # ------------------------------------------------------------------ #
    # LLM
    # ------------------------------------------------------------------ #
    @property
    def llm(self) -> LLMProtocol:
        if self._llm is None:
            self._llm = LLMClient(self.settings)
        return self._llm

    def _complete(self, prompt: str, system: str) -> str:
        return self.llm.complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ]
        )

    def _usage(self) -> dict[str, Any]:
        usage = getattr(self.llm, "usage", None)
        return usage() if callable(usage) else {}

    def _usage_delta(
        self, before: dict[str, Any]
    ) -> dict[str, Any]:
        """单次 run 的 token 消耗（同一 workflow 可能连续处理多个问题）。"""
        now = self._usage()
        if not now:
            return {}
        delta = dict(now)
        for key in ("calls", "prompt_tokens", "completion_tokens", "total_tokens"):
            if key in now and key in before:
                delta[key] = int(now[key]) - int(before[key])
        return delta

    def close(self) -> None:
        self.parser.close()

    def __enter__(self) -> "SQLAgentWorkflow":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # 各步骤
    # ------------------------------------------------------------------ #
    def select_tables(self, question: str) -> tuple[list[str], str]:
        """步骤 2：让模型选择回答问题所需的表。"""
        prompt = render(
            TABLE_SELECT_PROMPT,
            question=question,
            table_overview=self.parser.table_overview_markdown(),
            relations=self.parser.relations_markdown(),
        )
        raw = self._complete(prompt, system="你是一个严谨的数据库专家，只输出 JSON。")

        payload = extract_json(raw) or {}
        selected_raw = payload.get("tables", [])
        reason = str(payload.get("reason", "")).strip()

        # 大小写/空白容错，并过滤不存在的表
        name_map = {name.lower(): name for name in self.parser.table_names}
        if isinstance(selected_raw, str):
            selected_raw = [selected_raw]
        selected: list[str] = []
        for name in selected_raw if isinstance(selected_raw, list) else []:
            key = str(name).strip().lower()
            if key in name_map and name_map[key] not in selected:
                selected.append(name_map[key])

        if not selected:
            selected = list(self.parser.table_names)
            reason = reason or "模型未返回有效表名，回退为全表选择"

        return selected, reason

    def generate_sql(self, question: str, tables: Sequence[str]) -> str:
        """步骤 3：根据 schema 生成 SQL。"""
        schema = self.parser.schema_markdown(tables)
        prompt = render(
            SQL_GENERATION_PROMPT,
            question=question,
            schema=schema,
            relations=self.parser.relations_markdown(),
            max_rows=self.settings.max_rows,
        )

        last_error = ""
        for _ in range(2):  # 模型偶尔只输出解释，给它一次自我纠正机会
            raw = self._complete(
                prompt, system="你是一个只输出 SQLite 只读查询的工程师。"
            )
            sql = extract_sql(raw)
            if sql:
                return sql
            last_error = "模型没有返回可识别的 SQL"

        raise WorkflowError(last_error)

    def repair_sql(
        self,
        question: str,
        tables: Sequence[str],
        sql: str,
        error: str,
        history: Sequence[dict[str, str]],
    ) -> str:
        """步骤 5：带着报错信息改写 SQL。"""
        schema = self.parser.schema_markdown(tables)
        history_text = (
            "\n\n".join(
                f"{idx}. SQL: {item['sql']}\n   错误: {item['error']}"
                for idx, item in enumerate(history, start=1)
            )
            or "（无）"
        )

        prompt = render(
            SQL_REPAIR_PROMPT,
            question=question,
            schema=schema,
            relations=self.parser.relations_markdown(),
            sql=sql,
            error=error,
            history=history_text,
        )
        raw = self._complete(
            prompt, system="你是一个擅长修复 SQLite 报错的工程师，只输出一条修正后的 SQL。"
        )
        repaired = extract_sql(raw)
        if not repaired:
            raise WorkflowError("SQL 改写失败：模型没有返回可识别的 SQL")
        return repaired

    def validate_sql(self, sql: str) -> tuple[bool, str]:
        """步骤 4：只读校验 + 编译校验。"""
        return self.parser.validate_sql(sql)

    def execute_sql(
        self, sql: str
    ) -> tuple[list[str], list[tuple[Any, ...]], bool]:
        """步骤 6：只读执行，限制返回行数并设置超时。"""
        return self.parser.execute_sql(
            sql,
            max_rows=self.settings.max_rows,
            timeout=self.settings.query_timeout,
        )

    def summarize(
        self,
        question: str,
        sql: str,
        columns: Sequence[str],
        rows: Sequence[tuple[Any, ...]],
        truncated: bool,
    ) -> str:
        """步骤 7：把结果转换为自然语言。"""
        rows_md = rows_to_markdown(columns, rows, max_rows=50)
        prompt = render(
            RESULT_SUMMARY_PROMPT,
            language=self.settings.language,
            question=question,
            sql=sql,
            columns=", ".join(columns) if columns else "（无）",
            row_count=len(rows),
            truncated="是" if truncated else "否",
            rows=rows_md,
            max_rows=self.settings.max_rows,
        )
        return self._complete(
            prompt, system="你是一个严谨的数据分析师，只依据给定数据回答。"
        )

    # ------------------------------------------------------------------ #
    # 主流程
    # ------------------------------------------------------------------ #
    def run(
        self,
        question: str,
        tables: Optional[Sequence[str]] = None,
        max_repair: Optional[int] = None,
    ) -> SQLAgentResult:
        """执行完整工作流。"""
        question = (question or "").strip()
        if not question:
            raise ValueError("问题不能为空")

        result = SQLAgentResult(question=question)
        repair_limit = self.settings.max_repair if max_repair is None else max_repair
        usage_before = self._usage()

        def record(name: str, started: float, detail: str = "") -> None:
            trace = StepTrace(name=name, elapsed=time.perf_counter() - started, detail=detail)
            result.traces.append(trace)
            if self.on_step is not None:
                self.on_step(trace)

        # ---------------- 1. 数据库解析 ---------------- #
        started = time.perf_counter()
        table_count = len(self.parser.table_names)
        # 触发一次字段统计，确保“解析”步骤真的完成
        self.parser.parse_all()
        field_count = sum(
            len(self.parser.get_table_fields(name)) for name in self.parser.table_names
        )
        record(
            "数据库解析",
            started,
            f"{table_count} 张表 / {field_count} 个字段 / "
            f"{len(self.parser.get_data_relations())} 个外键",
        )

        try:
            # ---------------- 2. table 选择 ---------------- #
            started = time.perf_counter()
            if tables:
                wanted = [str(t).strip() for t in tables if str(t).strip()]
                missing = [t for t in wanted if t not in self.parser.table_names]
                if missing:
                    raise WorkflowError(
                        f"指定的表不存在：{', '.join(missing)}；"
                        f"可用表：{', '.join(self.parser.table_names)}"
                    )
                result.tables = list(dict.fromkeys(wanted))
                record("table 选择", started, f"用户指定：{', '.join(result.tables)}")
            else:
                result.tables, reason = self.select_tables(question)
                record(
                    "table 选择",
                    started,
                    f"{', '.join(result.tables)}（{reason or '无说明'}）",
                )

            # ---------------- 3. SQL 生成 ---------------- #
            started = time.perf_counter()
            result.sql = self.generate_sql(question, result.tables)
            record("SQL 生成", started, result.sql)

            # ---------------- 4/5/6. 校验 -> 改写 -> 执行 ---------------- #
            history: list[dict[str, str]] = []

            for attempt in range(repair_limit + 1):
                result.attempts = attempt + 1

                started = time.perf_counter()
                ok, message = self.validate_sql(result.sql)
                record(
                    "SQL 校验",
                    started,
                    "通过" if ok else message,
                )

                if ok:
                    started = time.perf_counter()
                    try:
                        columns, rows, truncated = self.execute_sql(result.sql)
                        result.columns = columns
                        result.rows = rows
                        result.truncated = truncated
                        record(
                            "SQL 执行",
                            started,
                            f"{len(rows)} 行"
                            + ("（已截断）" if truncated else ""),
                        )
                        break
                    except DBParserError as exc:
                        ok, message = False, str(exc)
                        record("SQL 执行", started, message)

                if attempt >= repair_limit:
                    result.error = f"SQL 在 {repair_limit + 1} 次尝试后仍失败：{message}"
                    break

                history.append({"sql": result.sql, "error": message})
                started = time.perf_counter()
                try:
                    result.sql = self.repair_sql(
                        question, result.tables, result.sql, message, history
                    )
                    record(
                        "SQL 改写",
                        started,
                        f"第 {attempt + 1} 次改写：{result.sql}",
                    )
                except (WorkflowError, LLMError) as exc:
                    result.error = f"SQL 改写失败：{exc}"
                    record("SQL 改写", started, result.error)
                    break

            if result.error:
                result.answer = f"抱歉，未能生成可执行的 SQL。原因：{result.error}"
                result.llm_usage = self._usage_delta(usage_before)
                return result

            # ---------------- 7. 结果汇总 ---------------- #
            started = time.perf_counter()
            try:
                result.answer = self.summarize(
                    question,
                    result.sql,
                    result.columns,
                    result.rows,
                    result.truncated,
                )
                record("结果汇总", started, result.answer)
            except LLMError as exc:
                result.error = f"结果汇总失败：{exc}"
                result.answer = self._fallback_summary(result)
                record("结果汇总", started, result.error)

            result.llm_usage = self._usage_delta(usage_before)
            return result

        except (LLMError, DBParserError, WorkflowError) as exc:
            result.error = str(exc)
            result.answer = f"抱歉，处理失败：{exc}"
            result.llm_usage = self._usage_delta(usage_before)
            return result

    @staticmethod
    def _fallback_summary(result: SQLAgentResult) -> str:
        if not result.rows:
            return "查询执行成功，但没有返回数据。"
        return (
            f"查询执行成功，返回 {len(result.rows)} 行"
            + ("（已截断）" if result.truncated else "")
            + "。原始结果：\n\n"
            + rows_to_markdown(result.columns, result.rows, max_rows=30)
        )
