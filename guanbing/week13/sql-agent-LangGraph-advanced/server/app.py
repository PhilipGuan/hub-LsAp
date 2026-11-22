"""生产边界层：把两条路线暴露成 HTTP 接口，并做上线硬化。

两条路线共用同一套 `/api/session` 建会话、`/api/chat` 提问、`/api/chat/resume` 续接：
- `route="graph"`（默认）→ `ConversationBI`：手写会话图，SQL 由固定流水线产出；
- `route="agent"` → `AgentBI`：`create_agent` 自主路线，挂 `HumanInTheLoopMiddleware`
  （`run_sql` 前停下等放行）+ `ModelFallbackMiddleware`（模型兜底）。

边界层只负责「图上不关心的那些事」（对应 `PRODUCTION.md` 的决策）：
- **D15 响应外壳（envelope）**：`{session_id, trace_id, route, status, degraded, events, trace}`，
  其中 `trace` 的字段逐字不变（仍由 `ConversationBI` / `AgentBI` 产出）；
- **D14 JSON 行日志 + trace_id**：每轮一条，含耗时 / SQL / 行数 / 错误类名；
- **D6 分层错误处理**：可预期失败标 `degraded`（话术见 `messages.py`），不可预期失败 → 500 + `trace_id`；
- **D11 阈值**：`request_timeout` / `MAX_ROWS` / `MAX_CLARIFY` 落在核心库（受控例外），边界层负责把
  「模型侧不可用（超时 / 429 / 连接失败）」这一步按 D6 降级成可读话术，而不是任由它冒到 500；
- **D8 状态存放**：检查点换 `SqliteSaver`，与会话元数据 / 配额 / 审计**共用一个 SQLite 文件**
  （读写都在 `server/store.py`）；`session_id` 对外、`thread_id` 对内，两者解耦 ——
  于是**进程重启后旧 `session_id` 仍可续问**；
- **D10 准入**：共享口令 `APP_PASSCODE`（校验后下发 httpOnly cookie）；
- **D13 只读数据库**：靠部署时的 `DB_URL`（见 `.env.example`），代码不动 `sqlagent/db.py`；
- **中英双语**：语言是**会话属性**（`sessions.lang`），建会话时由前端传入并落库；请求头
  `X-Lang` 只在「还不知道是哪个会话」的失败（口令 / 500 兜底）里兜底。默认一律 `zh`，
  所以核心库与 API 的既有行为逐字不变（`DESIGN.md` §4）；产品网页默认英文，由前端显式传 `en`。

要点：**FastAPI 只是边界层，不是架构**——图跑在同一进程内（同进程函数调用），没有网络跳。

跑法（在 `sql-agent-LangGraph-advanced` 目录下）：
    python -m uvicorn server.app:app --reload --port 8000
浏览器打开 http://127.0.0.1:8000/
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import openai
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from sqlagent import __version__
from sqlagent.agents.session import AgentBI
from sqlagent.config import get_db_url, redact
from sqlagent.conversation import ConversationBI
from sqlagent.db import DBParser
from sqlagent.i18n import normalise
from sqlagent.session import make_checkpointer
from sqlagent.time_travel import list_hops

from . import messages, store

STATIC_DIR = Path(__file__).resolve().parent / "static"

# 状态文件（D8）：检查点 + 会话元数据 + 配额 + 审计都在这里，容器里指向读写挂卷。
STATE_DB_PATH_ENV = "STATE_DB_PATH"
DEFAULT_STATE_DB = "checkpoints.sqlite"

# 口令 cookie（D10）。有效期与会话保留期（D8 的 7 天）对齐，够面试官点开一次链接。
PASSCODE_COOKIE = "sqlagent_passcode"
PASSCODE_MAX_AGE = 7 * 24 * 3600

# 进程级单例：DBParser 构造时会逐表逐字段跑统计查询，绝不能每请求新建。
# 批 3 起多了两项：`checkpointer`（共享 `SqliteSaver`）与 `store`（会话元数据 / 配额 / 审计）。
_state: dict[str, Any] = {}
# 会话表的**进程内缓存**：`session_id -> {"bot": 会话对象, "route": 路线}`。
# 值是两类会话之一（都提供 ask / resume / turns / graph / config），server 无需区分。
# 缓存未命中时会按 `store` 里的元数据**重建** bot —— 检查点在 SQLite 里，
# 所以进程重启（缓存清空、文件还在）之后旧 `session_id` 依然能续问（D8）。
_sessions: dict[str, dict] = {}
_lock = threading.Lock()

_logger = logging.getLogger("sqlagent.server")

# D6 的那条分界线：**模型侧不可用**（单次超时 / 429 / 连接失败 / 主+兜底全挂）是可预期失败，
# 降级成话术 + `degraded`；其余异常仍走全局兜底的 500。
# 只认 LLM 客户端的错误类型（`openai` 是 `langchain-openai` 的硬依赖，两条路线最终都经由它），
# 不写成 `except Exception` —— 那就把 D6 的分层又抹平了。
_LLM_ERRORS = (openai.OpenAIError,)


def _setup_logging() -> None:
    """把边界层日志配成「一行一条 JSON」，便于采集（D14）。"""
    if _logger.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False


def _emit(**fields: Any) -> None:
    """写一条 JSON 行日志。

    `default=str` 兜住不可序列化的值——**日志绝不能反过来把请求搞挂**。
    """
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **fields}
    _logger.info(json.dumps(record, ensure_ascii=False, default=str))


def _state_db_path() -> str:
    return os.environ.get(STATE_DB_PATH_ENV, "").strip() or DEFAULT_STATE_DB


def _passcode() -> str:
    """共享口令（D10）。留空 = 闸门关闭（本地开发）；上线必填。"""
    return os.environ.get("APP_PASSCODE", "").strip()


def _request_lang(request: Request) -> str:
    """从请求头 `X-Lang` 解析语言（前端每次请求都带）。

    会话级失败（配额 / 降级）优先用**会话自身的语言**（落在 `sessions.lang`）；只有
    「还不知道是哪个会话」的失败（口令、500 兜底）才走这里，故默认 `zh` 与核心库一致。
    """
    return normalise(request.headers.get("X-Lang"))


def _make_checkpointer(path: str) -> Any:
    """共享的 `SqliteSaver`（D8）。

    `make_checkpointer` 是核心库的工厂（`sqlagent/session.py`，本批不动它）。这里额外补一件
    边界层才知道的事：给连接设 `busy_timeout`——检查点与会话元数据 / 审计同库不同表，
    写锁竞争时应当等待片刻，而不是立刻抛 `database is locked`（`PRODUCTION.md` §7 风险 4）。
    """
    checkpointer = make_checkpointer("sqlite", path)
    checkpointer.conn.execute("PRAGMA busy_timeout=5000")
    return checkpointer


@asynccontextmanager
async def lifespan(_: FastAPI):
    _setup_logging()
    db_url = get_db_url()
    _state["parser"] = DBParser(db_url, verbose=False)

    state_path = _state_db_path()
    # 先连 store：它会建三张表并把文件切成 WAL（文件级设置，对检查点连接同样生效）。
    _state["store"] = store.Store(store.connect(state_path))
    _state["checkpointer"] = _make_checkpointer(state_path)

    # 启动时清理超期会话（D8）：元数据由 store 删，检查点交给 checkpointer。
    expired = _state["store"].prune_expired()
    for thread_id in expired:
        _state["checkpointer"].delete_thread(thread_id)

    _emit(event="startup", db_url=redact(db_url), tables=len(_state["parser"].table_names),
          state_db=redact(state_path), passcode="on" if _passcode() else "off",
          pruned_sessions=len(expired))
    yield
    _sessions.clear()
    checkpointer = _state.get("checkpointer")
    if checkpointer is not None:
        checkpointer.conn.close()
    state_store = _state.get("store")
    if state_store is not None:
        state_store.close()
    _state.clear()


app = FastAPI(title="SQL Agent · LangGraph", lifespan=lifespan)


@app.middleware("http")
async def assign_trace_id(request: Request, call_next):
    """给每个请求分配 `trace_id`，并回写到响应头——用户复述它就能在日志里对到那一轮。"""
    request.state.trace_id = uuid.uuid4().hex[:12]
    response = await call_next(request)
    response.headers["X-Trace-Id"] = request.state.trace_id
    return response


class SessionIn(BaseModel):
    """建会话。

    - `route="graph"`（默认）：手写会话图（`ConversationBI`，拓扑固定，SQL 生成后人工复核）；
    - `route="agent"`：`create_agent` 自主路线（`AgentBI`，模型自选工具顺序，
      由 `HumanInTheLoopMiddleware` 在 `run_sql` 前拦下等人工放行）。
    - `human_review=False` 时两条路线都不插人工闸门。
    - `lang`：面向用户话术与结论的语言（`zh` / `en`）。**默认 `zh`** —— 核心库与 API 的
      默认行为因此逐字不变（`DESIGN.md` §4）；产品网页默认英文，由前端在建会话时显式传 `en`。
    """

    human_review: bool = True
    route: Literal["graph", "agent"] = "graph"
    lang: str = "zh"


class ChatIn(BaseModel):
    session_id: str
    message: str = Field(min_length=1)


class AuthIn(BaseModel):
    """口令页提交（D10）。`lang` 只为把失败话术翻成对应语言。"""

    passcode: str = ""
    lang: str = "zh"


class ResumeIn(BaseModel):
    """把用户答复 / 人工决定送回暂停中的图。

    两条路线的续接值保持**各自框架原生格式**，server 不做归一化：
    - graph 路线（`ConversationBI`）：
      - 澄清（`interrupt.type == "clarify"`）→ `value` 传字符串；
      - 复核（`interrupt.type == "sql_review"`）→ `value` 传
        `{"action": "approve" | "edit" | "reject", "sql"?: str, "note"?: str}`。
    - agent 路线（`AgentBI`）→ `value` 传
      `{"decisions": [{"type": "approve" | "edit" | "reject" | "respond", ...}, ...]}`
      （`HumanInTheLoopMiddleware` 原生格式，每个被拦下的工具调用一项）。
    """

    session_id: str
    value: Any


# --------------------------------------------------------------------------- #
# 准入（D10）/ 取会话 / 判定 / 成壳
# --------------------------------------------------------------------------- #

def _require_passcode(request: Request) -> None:
    """口令闸门（D10）：校验通过后下发 httpOnly cookie，之后每次请求带着它。

    未配置 `APP_PASSCODE` 时闸门关闭 —— 本地开发不该被口令挡住；上线时必填（见 `.env.example`）。
    """
    expected = _passcode()
    if not expected:
        return
    given = request.cookies.get(PASSCODE_COOKIE, "")
    if not hmac.compare_digest(given, expected):
        raise HTTPException(status_code=401,
                            detail=messages.text("passcode_required", _request_lang(request)))


def _build_bot(thread_id: str, route: str, human_review: bool, lang: str) -> Any:
    """装配一个会话对象（两条路线共用同一批零件）。

    `checkpointer` 是进程内共享的那一个：图的记忆因此落在 SQLite 里，而不是本进程内存，
    这正是「重启后旧 `session_id` 仍可续问」的前提。
    `lang` 一路透传到图 / agent 的提示词与固定话术，决定整轮对话的语言。
    """
    common = {"thread_id": thread_id, "checkpointer": _state["checkpointer"],
              "human_review": human_review, "lang": lang}
    if route == "agent":
        return AgentBI(_state["parser"], **common)
    return ConversationBI(_state["parser"], **common)


def _entry(session_id: str, lang: str = "zh") -> dict:
    """取会话：先看进程内缓存，未命中则按元数据**重建**（重启后走的就是这条路）。

    重建靠的是 SQLite 里的两样东西：`store` 的元数据（路线 / 人工复核 / 语言 / `thread_id`）
    与检查点里的图状态 —— 两者都在，重建出来的 bot 接着上一次的状态继续，用户无感。
    `lang` 只用于「会话不存在」这条话术（此会话的语言已无从查起，用请求头的语言兜）。
    """
    entry = _sessions.get(session_id)
    if entry is not None:
        return entry
    meta = _state["store"].get_session(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=messages.text("session_not_found", lang))
    entry = {"bot": _build_bot(meta["thread_id"], meta["route"], meta["human_review"],
                               meta["lang"]),
             "route": meta["route"], "lang": meta["lang"]}
    with _lock:
        _sessions[session_id] = entry
    return entry


def _bot(session_id: str, lang: str = "zh") -> Any:
    return _entry(session_id, lang)["bot"]


def _reserve_round(session_id: str, lang: str) -> None:
    """配额闸门（D11）：每日全局 200 轮 / 单会话 20 轮，用尽则 429 + 话术。"""
    reason = _state["store"].reserve_round(session_id)
    if reason == "global":
        raise HTTPException(status_code=429, detail=messages.text("quota_exceeded_global", lang))
    if reason == "session":
        raise HTTPException(status_code=429, detail=messages.text("quota_exceeded_session", lang))


def _is_degraded(trace: dict, route: str) -> bool:
    """可预期失败的判定（D6）。

    尺子：
    - 暂停（`clarify` / `sql_review`）不是失败；
    - 图路线的 `out`（谢绝）是**业务正常分支**，也不算失败；
    - `ambiguous` 一旦走到「已完成」，只可能是澄清额度用尽（`MAX_CLARIFY`，否则会停在
      `interrupted`），因此归入降级；
    - 其余 `ok=False` 的情形（生成失败、执行失败、改写到上限、复核驳回）视作降级。

    agent 路线拿不到可比的 `ok` 信号，改用「有没有产出结论」当判据：模型一句话都没给出，
    就按降级处理（话术由模型侧不可用那条兜住）。
    """
    if (trace or {}).get("status") == "interrupted":
        return False
    if route == "graph":
        if (trace.get("scope") or {}).get("scope") == "out":
            return False
        return not trace.get("ok")
    return not trace.get("answer")


def _degraded_trace(raw: str | None, route: str, lang: str) -> dict:
    """模型侧不可用时的降级 trace（D6）：键与正常 trace 同形，`answer` 换成话术。

    保持同形是为了让前端不必为「降级」再写一套渲染分支（D15 的初衷）。
    """
    trace: dict[str, Any] = {
        "raw_question": raw,
        "standalone_question": None,
        "scope": None,
        "tables": None,
        "sql": None,
        "ok": False,
        "columns": None,
        "rows": None,
        "answer": messages.text("llm_unavailable", lang),
        "status": "done",
    }
    if route == "agent":
        trace["route"] = "agent"     # agent 路线的 trace 带 `route`、不带 `attempts`
    else:
        trace["attempts"] = 0
    return trace


def _finish(request: Request, session_id: str, route: str, trace: dict, started: float,
            *, degraded: bool | None = None, error: str | None = None) -> dict:
    """把一轮结果包成 envelope（D15），并落一条结构化日志（D14）+ 一行审计（D14）。

    `degraded` 留了显式覆盖口：合成出来的降级 trace 靠 `_is_degraded` 判不出来（它认的是
    核心库的字段组合），由调用方直接告诉本函数「这就是降级」。
    `error` 是可预期失败的类名（如 `APITimeoutError`），进审计表供事后归因。
    """
    trace_id = getattr(request.state, "trace_id", "-")
    degraded = _is_degraded(trace, route) if degraded is None else degraded
    status = trace.get("status") or "done"
    if status != "interrupted" and degraded:
        status = "degraded"

    rows = trace.get("rows")
    row_count = len(rows) if isinstance(rows, list) else None
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    _emit(event="round", trace_id=trace_id, session_id=session_id, route=route, status=status,
          degraded=degraded, elapsed_ms=elapsed_ms, error=error,
          sql=trace.get("sql"), rows=row_count)
    _state["store"].record_audit(trace_id=trace_id, session_id=session_id, route=route,
                                 status=status, degraded=degraded, elapsed_ms=elapsed_ms,
                                 sql=trace.get("sql"), rows=row_count, error=error)

    return {
        "session_id": session_id,
        "trace_id": trace_id,
        "route": route,
        "status": status,
        "degraded": degraded,
        "events": [{"type": "final"}],
        "trace": trace,
    }


def _run_round(request: Request, session_id: str, route: str, raw: str | None, call: Any,
               lang: str) -> dict:
    """跑一轮（`call` 是 `bot.ask` / `bot.resume` 的闭包）。

    唯一的额外职责：把「模型侧不可用」按 D6 降级成话术。`request_timeout=30`（D11）落地后，
    超时是最常见的可预期失败，不该让它变成 500；**失败必落一条结构化日志**（D6）。
    `lang` 决定降级话术的语言（取会话自身的语言）。
    """
    started = time.perf_counter()
    try:
        trace = call()
    except _LLM_ERRORS as exc:
        error = type(exc).__name__
        _emit(event="llm_error", level="warn", trace_id=getattr(request.state, "trace_id", "-"),
              session_id=session_id, route=route, error=error, detail=str(exc)[:300])
        return _finish(request, session_id, route, _degraded_trace(raw, route, lang), started,
                       degraded=True, error=error)
    return _finish(request, session_id, route, trace, started)


# --------------------------------------------------------------------------- #
# 路由
# --------------------------------------------------------------------------- #

@app.get("/api/auth")
def auth_status(request: Request) -> dict:
    """口令闸门状态（D10）：前端据此决定「先显示口令页」还是「直接开聊」（D17 ①）。

    `required=False` 表示服务没配口令（本地开发），任何人都能直接进。
    """
    expected = _passcode()
    given = request.cookies.get(PASSCODE_COOKIE, "")
    return {"required": bool(expected),
            "ok": (not expected) or hmac.compare_digest(given, expected)}


@app.post("/api/auth")
def auth(body: AuthIn, response: Response) -> dict:
    """提交口令：对了就下发 httpOnly cookie，之后所有可写接口都认它。"""
    expected = _passcode()
    if not expected:
        return {"ok": True, "required": False}
    if not hmac.compare_digest(body.passcode.strip(), expected):
        raise HTTPException(status_code=401,
                            detail=messages.text("passcode_invalid", normalise(body.lang)))
    response.set_cookie(PASSCODE_COOKIE, expected, max_age=PASSCODE_MAX_AGE,
                        httponly=True, samesite="lax", path="/")
    return {"ok": True, "required": True}


@app.post("/api/session", dependencies=[Depends(_require_passcode)])
def create_session(body: SessionIn | None = None) -> dict:
    """新建一个会话（= 一个 `thread_id`），返回前端要保存的 `session_id`。

    `session_id`（对外）与 `thread_id`（对内、图的主键）**分两列存**（D8）：
    将来想轮换对外 ID 而不动图状态，只需改 `sessions` 表里的一行。
    `lang` 一并落库，进程重启后按它重建 bot（见 `_entry`），语言不丢。
    """
    body = body or SessionIn()
    lang = normalise(body.lang)
    thread_id = f"web-{uuid.uuid4().hex[:8]}"
    bot = _build_bot(thread_id, body.route, body.human_review, lang)
    session_id = uuid.uuid4().hex
    _state["store"].create_session(session_id, thread_id, body.route, body.human_review, lang=lang)
    with _lock:
        _sessions[session_id] = {"bot": bot, "route": body.route, "lang": lang}
    return {"session_id": session_id, "thread_id": thread_id, "route": body.route,
            "human_review": body.human_review, "lang": lang}


@app.post("/api/chat", dependencies=[Depends(_require_passcode)])
def chat(body: ChatIn, request: Request) -> dict:
    """一轮提问。图若暂停（澄清 / 复核），envelope 的 `status="interrupted"` + `trace.interrupt` 载荷。"""
    lang = _request_lang(request)
    entry = _entry(body.session_id, lang)
    lang = entry.get("lang") or lang
    _reserve_round(body.session_id, lang)
    return _run_round(request, body.session_id, entry["route"], body.message,
                      lambda: entry["bot"].ask(body.message), lang)


@app.post("/api/chat/resume", dependencies=[Depends(_require_passcode)])
def resume(body: ResumeIn, request: Request) -> dict:
    """把用户答复 / 人工决定送回暂停中的图，继续跑到下一次暂停或结束。"""
    lang = _request_lang(request)
    entry = _entry(body.session_id, lang)
    lang = entry.get("lang") or lang
    _reserve_round(body.session_id, lang)
    return _run_round(request, body.session_id, entry["route"], None,
                      lambda: entry["bot"].resume(body.value), lang)


@app.get("/api/session/{session_id}", dependencies=[Depends(_require_passcode)])
def session_detail(session_id: str, request: Request) -> dict:
    """会话历史（读自检查点）+ 检查点轨迹（LangGraph 时间旅行 API）。"""
    bot = _bot(session_id, _request_lang(request))
    _state["store"].touch(session_id)
    return {
        "thread_id": bot.thread_id,
        "turns": bot.turns,
        "trajectory": list_hops(bot.graph, bot.config),
    }


@app.get("/health")
def health() -> dict:
    """存活 + 就绪探测（D16）：版本、表数、状态库可写性。

    `state_db_writable=False` 时整体判**不健康**：状态库写不进去，服务只是「页面还能打开」而已，
    监控与进程管理器该据此告警。探测手法见 `store.Store.writable()`（真写一次、不碰业务数据）。
    """
    writable = _state["store"].writable()
    return {"ok": writable, "version": __version__,
            "tables": len(_state["parser"].table_names), "state_db_writable": writable}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# --------------------------------------------------------------------------- #
# 错误兜底（D6）
# --------------------------------------------------------------------------- #

@app.exception_handler(HTTPException)
async def _http_error(request: Request, exc: HTTPException) -> JSONResponse:
    """可预期的 4xx：保留 `detail` 话术，并带上 `trace_id`（失败必落日志）。"""
    trace_id = getattr(request.state, "trace_id", "-")
    _emit(event="http_error", level="warn", trace_id=trace_id, path=str(request.url.path),
          status=exc.status_code, detail=str(exc.detail))
    return JSONResponse(status_code=exc.status_code,
                        content={"detail": exc.detail, "trace_id": trace_id})


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    """不可预期失败：500 + 兜底话术 + `trace_id`，细节只进日志（不回显给用户）。

    这个响应由最外层的 `ServerErrorMiddleware` 直接发出，**不经过 trace 中间件**，
    所以这里要自己补上 `X-Trace-Id` 头（否则「响应头里有 trace_id」这条约定在这里断掉）。
    """
    trace_id = getattr(request.state, "trace_id", "-")
    _emit(event="unhandled", level="error", trace_id=trace_id, path=str(request.url.path),
          error=type(exc).__name__, detail=str(exc)[:500])
    response = JSONResponse(
        status_code=500,
        content={"detail": messages.text("internal_error", _request_lang(request)),
                 "trace_id": trace_id})
    response.headers["X-Trace-Id"] = trace_id
    return response
