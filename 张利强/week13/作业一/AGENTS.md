# AGENTS.md — week13/作业一

## Scope

- Work only inside this folder. Do not modify or consult other week folders.
- `ref/` is teacher-provided reference material (`sql-agent.ipynb`, `agent-demo.py`, `chinook.db`) — read-only.

## Environment

- Use `/opt/anaconda3/envs/py312/bin/python` (Python 3.12). The plain `python3` on PATH is 3.13 and is not the project interpreter.
- Installed: `langgraph 1.2`, `langchain`, `langchain-openai`, `sqlalchemy 2.0`, `pandas`.
- LLM access: DashScope OpenAI-compatible endpoint `https://dashscope.aliyuncs.com/compatible-mode/v1`; key from `DASHSCOPE_API_KEY`, model from `OPENAI_MODEL` (default `qwen-plus`).

## Implementation

- `sql_agent.py` is the deliverable: a LangGraph `StateGraph` implementing
  `select_table -> generate_sql -> validate_sql -> (rewrite_sql loop) -> execute_sql -> summarize`,
  with a `give_up` branch when no table or too many failed rewrites.
- Agents are declared as `StructuredAgent(name, instructions, output_type, model)`, mirroring `ref/agent-demo.py`; internally they use `with_structured_output(..., method="function_calling")` because DashScope does not reliably support `json_schema`.
- `DBParser` follows `ref/sql-agent.ipynb` but is read-only: only a single `SELECT` / `WITH` is allowed and the engine never commits, so `ref/chinook.db` cannot be modified.
- DB path is resolved from `__file__` to `ref/chinook.db`; do not hard-code CWD-relative paths.
- `DBParser.__init__` scans every table and column, printing one `Table -> ...` line per table on each run (slow, noisy, expected).

## Verify

- `python -m py_compile sql_agent.py`
- Live run: `python sql_agent.py "哪个国家的客户最多？"` — requires a valid `DASHSCOPE_API_KEY` (the key currently in the environment returns 401).
- No test suite exists; stub the chat model to exercise both the success and rewrite paths offline.
