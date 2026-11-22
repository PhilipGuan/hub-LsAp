"""SQL Agent · LangChain 版

把 `04_SQL-Code-Agent-Demo/sql-agent-workflow.ipynb`（裸 OpenAI SDK + 手写编排）
用 LangChain / LangGraph 重写后的可复用包。

两条并列的实现路径（共用同一套内核：db / guard / tools / judge）：
- `sqlagent.workflow` —— LangGraph 显式状态图，复刻原版 7 阶段确定性流水线；
- `sqlagent.agents`   —— LangChain `create_agent`，复刻参考实现 `sql-agent.ipynb` 的三角色 Agent。

面向用户的多轮入口是 `sqlagent.chatbi.ChatBI`；评测装置是 `sqlagent.evalset` + `sqlagent.judge`。

顶层只做「懒导出」，避免 `import sqlagent` 就牵出 matplotlib / IPython 等重依赖。
"""

__version__ = "1.0.0"

# 需要懒导出的名字 → (子模块, 属性名)
_LAZY = {
    "ChatBI": ("sqlagent.chatbi", "ChatBI"),
    "render_result": ("sqlagent.chatbi", "render_result"),
    "scope_check": ("sqlagent.gates", "scope_check"),
    "DBParser": ("sqlagent.db", "DBParser"),
    "is_readonly": ("sqlagent.guard", "is_readonly"),
    "run_workflow": ("sqlagent.workflow.graph", "run_workflow"),
    "build_graph": ("sqlagent.workflow.graph", "build_graph"),
    "build_checkpointed_graph": ("sqlagent.workflow.graph", "build_checkpointed_graph"),
    # —— LangGraph 进阶三件套：持久化 / 人工介入 / 时间旅行 ——
    "ConversationBI": ("sqlagent.conversation.bi", "ConversationBI"),
    "build_conversation_graph": ("sqlagent.conversation.graph", "build_conversation_graph"),
    "make_checkpointer": ("sqlagent.session", "make_checkpointer"),
    "new_thread_id": ("sqlagent.session", "new_thread_id"),
    "thread_config": ("sqlagent.session", "thread_config"),
    "review_loop": ("sqlagent.hitl", "review_loop"),
    "pending_interrupt": ("sqlagent.hitl", "pending_interrupt"),
    "list_hops": ("sqlagent.time_travel", "list_hops"),
    "format_hops": ("sqlagent.time_travel", "format_hops"),
    "replay_from": ("sqlagent.time_travel", "replay_from"),
    "SQLAgent": ("sqlagent.agents.sql_agent", "SQLAgent"),
    "run_autonomous": ("sqlagent.agents.sql_agent", "run_autonomous"),
    "build_agent_middleware": ("sqlagent.agents.sql_agent", "build_agent_middleware"),
    # —— 批 5：SQL 生成子代理（仅 agent 路线，D21–D23）——
    "SqlDraft": ("sqlagent.agents.sql_agent", "SqlDraft"),
    "build_sql_subagent": ("sqlagent.agents.sql_agent", "build_sql_subagent"),
    "draft_sql": ("sqlagent.agents.sql_agent", "draft_sql"),
    "make_write_sql_tool": ("sqlagent.agents.sql_agent", "make_write_sql_tool"),
    "AgentBI": ("sqlagent.agents.session", "AgentBI"),
    "JudgeAgent": ("sqlagent.judge", "JudgeAgent"),
    "run_and_judge": ("sqlagent.judge", "run_and_judge"),
    "GOLDEN_SET": ("sqlagent.evalset", "GOLDEN_SET"),
    "freeze_golden": ("sqlagent.evalset", "freeze_golden"),
    "STRUCTURE_PROBES": ("sqlagent.evalset", "STRUCTURE_PROBES"),
    "freeze_probes": ("sqlagent.evalset", "freeze_probes"),
    "QuestionSimulator": ("sqlagent.evalset", "QuestionSimulator"),
    "get_db_url": ("sqlagent.config", "get_db_url"),
}

__all__ = ["__version__", *_LAZY]


def __getattr__(name: str):
    """PEP 562 懒加载：`sqlagent.ChatBI` 按需 import。"""
    if name in _LAZY:
        import importlib
        module_name, attr = _LAZY[name]
        value = getattr(importlib.import_module(module_name), attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
