"""validate_sql / execute_sql 节点：确定性组件，不经过 LLM。

- validate_sql 调用原样复用的 SQLValidator（安全门禁 + EXPLAIN 编译校验）。
- execute_sql 调用原样复用的 SQLExecutor；执行失败时把错误写回 validation，
  让执行错误与校验错误走同一条路由通道（等价于原实现的异常转换）。
"""

from __future__ import annotations

import logging
import time

from ..graph_state import AgentState
from ..models import ValidationResult

logger = logging.getLogger(__name__)


def _preview(text: str, limit: int = 500) -> str:
    compact = " ".join(str(text).split())
    return compact if len(compact) <= limit else compact[:limit] + "..."


def make_validate_sql_node(validator):
    def validate_sql(state: AgentState) -> dict:
        sql = state.get("sql", "")
        started = time.perf_counter()
        result = validator.validate(sql)
        logger.info(
            "stage=validate ok=%s code=%s elapsed=%.3fs message=%s",
            result.ok,
            result.code,
            time.perf_counter() - started,
            _preview(result.message),
        )
        return {"validation": result}

    return validate_sql


def make_execute_sql_node(executor):
    def execute_sql(state: AgentState) -> dict:
        validation = state["validation"]
        sql = validation.normalized_sql or state.get("sql", "")

        started = time.perf_counter()
        try:
            execution = executor.execute(sql)
        except Exception as exc:  # SQLExecutionError 及其他执行期异常
            logger.warning(
                "stage=execute ok=false elapsed=%.3fs message=%s",
                time.perf_counter() - started,
                _preview(str(exc)),
            )
            return {
                "validation": ValidationResult(
                    ok=False,
                    code=getattr(exc, "code", None),
                    message=str(exc),
                    normalized_sql=sql,
                ),
                "execution": None,
            }

        logger.info(
            "stage=execute ok=true rows=%s truncated=%s elapsed=%.3fs",
            execution.row_count,
            execution.truncated,
            time.perf_counter() - started,
        )
        # 执行成功后以规范化 SQL 为准（与原实现一致）。
        return {"execution": execution, "sql": sql}

    return execute_sql
