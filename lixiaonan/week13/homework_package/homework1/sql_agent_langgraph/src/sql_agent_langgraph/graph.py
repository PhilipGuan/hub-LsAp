"""图的装配与运行入口。

拓扑（见 DESIGN_sql_agent_langgraph.md §3）：

    START -> select_tables -> route_selection -> (generate_sql | select_tables | finalize)
    generate_sql -> validate_sql -> route_validate -> (execute_sql | rewrite_sql | finalize)
    execute_sql  -> route_execute  -> (summarize | rewrite_sql | finalize)
    rewrite_sql  -> route_rewrite  -> (validate_sql | finalize)
    summarize -> finalize -> END
"""

from __future__ import annotations

import logging
import time
import uuid

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from .config import settings
from .db.catalog import SchemaCatalog, get_catalog
from .db.executor import SQLExecutor
from .db.validator import SQLValidator
from .graph_state import AgentState
from .llm import LLMBundle, default_llm_bundle
from .models import WorkflowOutput
from .nodes import (
    make_execute_sql_node,
    make_finalize_node,
    make_generate_sql_node,
    make_rewrite_sql_node,
    make_select_tables_node,
    make_summarize_node,
    make_validate_sql_node,
)
from .routing import (
    make_route_execute,
    make_route_selection,
    make_route_validate,
    route_after_rewrite,
)

logger = logging.getLogger(__name__)


def _default_checkpointer() -> InMemorySaver:
    """InMemorySaver + 显式 msgpack 允许清单。

    状态中的 Pydantic 契约（models.py）默认走 pickle 兜底反序列化，
    langgraph 已警告未来版本将直接拒绝。这里按 (module, 类名) 注册全部
    契约类并关闭 pickle 兜底：checkpoint 只能反序列化白名单内的类型，
    未注册类型会被明确阻止而不是静默变成 dict。
    """

    from pydantic import BaseModel

    from . import models as models_module

    contract_types = sorted(
        (
            obj
            for obj in vars(models_module).values()
            if isinstance(obj, type)
            and issubclass(obj, BaseModel)
            and obj.__module__ == models_module.__name__
        ),
        key=lambda cls: cls.__name__,
    )
    serde = JsonPlusSerializer(
        pickle_fallback=False,
        allowed_msgpack_modules=contract_types,
    )
    return InMemorySaver(serde=serde)


def build_graph(
    catalog: SchemaCatalog,
    validator: SQLValidator,
    executor: SQLExecutor,
    llms: LLMBundle,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    max_repair_attempts: int | None = None,
    max_selection_retries: int | None = None,
) -> CompiledStateGraph:
    """装配 StateGraph。依赖经闭包注入节点与路由，不进入图状态。"""

    max_repair = (
        settings.max_repair_attempts
        if max_repair_attempts is None
        else max_repair_attempts
    )
    max_select = (
        settings.max_selection_retries
        if max_selection_retries is None
        else max_selection_retries
    )

    builder = StateGraph(AgentState)
    builder.add_node("select_tables", make_select_tables_node(catalog, llms.selection))
    builder.add_node("generate_sql", make_generate_sql_node(catalog, llms.generation))
    builder.add_node("validate_sql", make_validate_sql_node(validator))
    builder.add_node("execute_sql", make_execute_sql_node(executor))
    builder.add_node(
        "rewrite_sql", make_rewrite_sql_node(llms.rewrite, max_repair)
    )
    builder.add_node(
        "summarize",
        make_summarize_node(llms.summary, settings.max_summary_rows),
    )
    builder.add_node("finalize", make_finalize_node(catalog))

    builder.add_edge(START, "select_tables")
    builder.add_conditional_edges(
        "select_tables",
        make_route_selection(catalog, max_select),
        ["select_tables", "generate_sql", "finalize"],
    )
    builder.add_edge("generate_sql", "validate_sql")
    builder.add_conditional_edges(
        "validate_sql",
        make_route_validate(max_repair),
        ["execute_sql", "rewrite_sql", "finalize"],
    )
    builder.add_conditional_edges(
        "execute_sql",
        make_route_execute(max_repair),
        ["summarize", "rewrite_sql", "finalize"],
    )
    builder.add_conditional_edges(
        "rewrite_sql", route_after_rewrite, ["validate_sql", "finalize"]
    )
    builder.add_edge("summarize", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)


class SQLAgentGraph:
    """对外运行入口，等价于原实现的 SQLAgentWorkflow。

    - Agent 模块级/构建期定义，流程由图控制；
    - checkpointer 默认开启（InMemorySaver），支持 --thread-id 与状态回放；
    - 分支、循环、重试和最终 JSON 全部由图的路由与 finalize 节点决定。
    """

    def __init__(
        self,
        catalog: SchemaCatalog | None = None,
        validator: SQLValidator | None = None,
        executor: SQLExecutor | None = None,
        llms: LLMBundle | None = None,
        *,
        checkpointer: BaseCheckpointSaver | None = None,
        max_repair_attempts: int | None = None,
        max_selection_retries: int | None = None,
    ) -> None:
        self.catalog = catalog or get_catalog()
        self.validator = validator or SQLValidator(self.catalog.db_path)
        self.executor = executor or SQLExecutor(self.catalog.db_path)
        self.llms = llms or default_llm_bundle()
        self.checkpointer = checkpointer or _default_checkpointer()
        self.graph = build_graph(
            self.catalog,
            self.validator,
            self.executor,
            self.llms,
            checkpointer=self.checkpointer,
            max_repair_attempts=max_repair_attempts,
            max_selection_retries=max_selection_retries,
        )

    # -- 输入守卫 ---------------------------------------------------------

    @staticmethod
    def _prepare(question: str) -> tuple[str, WorkflowOutput | None]:
        if not isinstance(question, str):
            raise TypeError("question 必须是字符串")
        question = question.strip()
        if not question:
            logger.warning("workflow ignored empty question")
            return question, WorkflowOutput(
                question=question, answer="请输入要查询的问题。"
            )
        return question, None

    @staticmethod
    def _config(thread_id: str | None) -> dict:
        return {"configurable": {"thread_id": thread_id or str(uuid.uuid4())}}

    # -- 运行 -------------------------------------------------------------

    def run(self, question: str, *, thread_id: str | None = None) -> WorkflowOutput:
        """整图执行，返回 WorkflowOutput（异常不外抛，保证 CLI 稳定输出）。"""

        question, early = self._prepare(question)
        if early is not None:
            return early

        started = time.perf_counter()
        logger.info(
            "workflow started question=%s primary_model=%s summary_model=%s",
            " ".join(question.split())[:300],
            settings.primary_model,
            settings.summary_model,
        )
        try:
            config = self._config(thread_id)
            final_state = self.graph.invoke(
                {"question": question, "attempts": []}, config
            )
            output: WorkflowOutput = final_state["output"]
            logger.info(
                "workflow finished elapsed=%.2fs tables=%s rows=%s repairs=%s error=%s",
                time.perf_counter() - started,
                output.tables,
                output.row_count,
                output.repair_attempts,
                output.error,
            )
            return output
        except Exception as exc:  # noqa: BLE001 - CLI 需要稳定输出
            logger.exception("SQL agent workflow failed")
            return WorkflowOutput(
                question=question,
                answer=f"处理失败：{exc}",
                error=str(exc),
            )

    def run_streaming(
        self,
        question: str,
        *,
        thread_id: str | None = None,
        on_update=None,
    ) -> WorkflowOutput:
        """逐节点流式执行。

        on_update(node_name, state_update) 会在每个节点完成后被调用
        （stream_mode="updates"，教程 02 的流式语义）；
        流结束后从 finalize 的更新中取 WorkflowOutput。
        """

        question, early = self._prepare(question)
        if early is not None:
            return early

        started = time.perf_counter()
        config = self._config(thread_id)
        output: WorkflowOutput | None = None
        try:
            for chunk in self.graph.stream(
                {"question": question, "attempts": []}, config, stream_mode="updates"
            ):
                for node, update in chunk.items():
                    if on_update is not None:
                        on_update(node, update)
                    if node == "finalize" and isinstance(update, dict):
                        candidate = update.get("output")
                        if candidate is not None:
                            output = candidate
            if output is None:
                # checkpointer 常开，可从终态恢复。
                output = self.graph.get_state(config).values.get("output")
            if output is None:
                raise RuntimeError("图执行结束但未产生 output")
            logger.info(
                "workflow finished elapsed=%.2fs tables=%s rows=%s repairs=%s error=%s",
                time.perf_counter() - started,
                output.tables,
                output.row_count,
                output.repair_attempts,
                output.error,
            )
            return output
        except Exception as exc:  # noqa: BLE001 - CLI 需要稳定输出
            logger.exception("SQL agent workflow failed")
            return WorkflowOutput(
                question=question,
                answer=f"处理失败：{exc}",
                error=str(exc),
            )

    # -- 调试辅助 ---------------------------------------------------------

    def state_history(self, thread_id: str):
        """回放入口（教程 04）：按时间倒序列出该线程的全部 checkpoint。"""

        config = self._config(thread_id)
        return list(self.graph.get_state_history(config))

    def mermaid(self) -> str:
        return self.graph.get_graph().draw_mermaid()
