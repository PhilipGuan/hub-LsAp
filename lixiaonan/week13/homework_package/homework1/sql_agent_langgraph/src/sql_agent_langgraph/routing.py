"""条件边路由函数：图的每条回边与终止路径都在这里，可脱离图单独单测。

路由函数是纯函数（只读状态、返回下一节点名），需要 catalog 等依赖的
通过工厂闭包注入。原实现 workflow.py 中的 for/if 编排逻辑在这里等价重建。
"""

from __future__ import annotations

import logging
from typing import Literal

logger = logging.getLogger(__name__)

SelectionRoute = Literal["select_tables", "generate_sql", "finalize"]
ValidateRoute = Literal["execute_sql", "rewrite_sql", "finalize"]
ExecuteRoute = Literal["summarize", "rewrite_sql", "finalize"]
RewriteRoute = Literal["validate_sql", "finalize"]


def selection_problems(tables: list[str], catalog) -> list[str]:
    """校验选表结果，返回问题描述列表；空列表表示通过。

    与原实现 SQLAgentWorkflow._selection_problems 一致。
    catalog 只需提供 table_names 与 is_connected(tables)。
    """

    if not tables:
        return ["未选择任何表"]

    unknown = sorted(set(tables) - set(catalog.table_names))
    if unknown:
        return [
            f"表不存在：{', '.join(unknown)}；"
            f"可选表：{', '.join(catalog.table_names)}"
        ]

    if not catalog.is_connected(tables):
        return ["所选表之间没有外键连接，请重新选择可以 JOIN 的表"]
    return []


def make_route_selection(catalog, max_selection_retries: int = 1):
    """select_tables 之后的路由：通过 / 反馈重试 / 终止。"""

    def route_selection(state) -> SelectionRoute:
        selection = state.get("selection")
        if selection is None:
            return "finalize"
        if selection.needs_clarification or not selection.tables:
            # 澄清 / 无法回答：直接终止，由 finalize 组装输出。
            return "finalize"

        if selection_problems(selection.tables, catalog):
            if state.get("selection_retries", 0) < max_selection_retries:
                return "select_tables"
            # 反馈重试后仍非法：终止并提示。
            return "finalize"
        return "generate_sql"

    return route_selection


def make_route_validate(max_repair_attempts: int = 3):
    """validate_sql 之后的路由：通过则执行；失败进入共享修复预算判定。"""

    def route_validate(state) -> ValidateRoute:
        validation = state.get("validation")
        if validation is not None and validation.ok:
            return "execute_sql"
        if state.get("repair_attempts", 0) < max_repair_attempts:
            return "rewrite_sql"
        return "finalize"

    return route_validate


def make_route_execute(max_repair_attempts: int = 3):
    """execute_sql 之后的路由：成功则汇总；运行错误进入共享修复预算判定。

    执行失败时 execute_sql 节点把错误写回 validation，
    因此这里只看 execution 是否存在。
    """

    def route_execute(state) -> ExecuteRoute:
        if state.get("execution") is not None:
            return "summarize"
        if state.get("repair_attempts", 0) < max_repair_attempts:
            return "rewrite_sql"
        return "finalize"

    return route_execute


def route_after_rewrite(state) -> RewriteRoute:
    """rewrite_sql 之后的路由：cannot_fix 终止；否则新 SQL 必须重新过校验。"""

    draft = state.get("draft")
    if draft is not None and draft.cannot_fix:
        return "finalize"
    return "validate_sql"
