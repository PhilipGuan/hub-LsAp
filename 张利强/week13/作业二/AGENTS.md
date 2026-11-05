# AGENTS.md — week13/作业一

## Scope

- Work only inside this folder. Do not modify or consult other week folders.
- `ref/` is teacher-provided reference material (`sql-agent.ipynb`, `agent-demo.py`, `chinook.db`) — read-only.

## Environment

- Use `/opt/anaconda3/envs/py312/bin/python` (Python 3.12). The plain `python3` on PATH is 3.13 and is not the project interpreter.
- Installed: `langgraph 1.2`, `langchain`, `langchain-openai`, `sqlalchemy 2.0`, `pandas`.
- LLM access: DashScope OpenAI-compatible endpoint; config lives in `sql_agent.py` (`DEFAULT_MODEL` = `qwen3.8-max-0902`, `DEFAULT_API_KEY`), shared through `create_llm()`. Override with `SQL_AGENT_MODEL` / `SQL_AGENT_API_KEY` / `SQL_AGENT_BASE_URL` — not `DASHSCOPE_API_KEY`, which may point at a stale global key.

## Implementation

- `sql_agent.py` is the deliverable: a LangGraph `StateGraph` implementing
  `select_table -> generate_sql -> validate_sql -> (rewrite_sql loop) -> execute_sql -> summarize`,
  with a `give_up` branch when no table or too many failed rewrites.
- Agents are declared as `StructuredAgent(name, instructions, output_type, model)`, mirroring `ref/agent-demo.py`; internally they use `with_structured_output(..., method="function_calling")` because DashScope does not reliably support `json_schema`.
- `DBParser` follows `ref/sql-agent.ipynb` but is read-only: only a single `SELECT` / `WITH` is allowed and the engine never commits, so `ref/chinook.db` cannot be modified.
- DB path is resolved from `__file__` to `ref/chinook.db`; do not hard-code CWD-relative paths.
- `DBParser.__init__` scans every table and column, printing one `Table -> ...` line per table on each run (slow, noisy, expected).

## Web app

- `server.py` is a FastAPI wrapper: `POST /api/chat` calls `SQLAgentWorkflow.run` and returns `answer/table/sql/columns/rows/elapsed_ms`; `GET /api/health`; serves `web/dist` if it exists.
- `web/` is a Vue 3 + Vite app. Dev server proxies `/api` to `127.0.0.1:8000`; CORS allows ports 5173.
- `web/node_modules` and `web/dist` are git-ignored. Rebuild with `cd web && npm install && npm run build` after editing the frontend.

## Verify

- `python -m py_compile sql_agent.py server.py`
- CLI: `python sql_agent.py "有多少张专辑？"`
- API: start `python -m uvicorn server:app --port 8000`, then `curl -X POST localhost:8000/api/chat -H 'Content-Type: application/json' -d '{"message":"有多少张专辑？"}'`
- Frontend: `cd web && npm run build` must succeed.
- No test suite exists; stub the chat model to exercise the success and rewrite paths offline.
