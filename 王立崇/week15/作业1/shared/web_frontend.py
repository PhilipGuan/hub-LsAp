"""前端进程的 HTTP 版实现：FastAPI + WebSocket。

替换原来的 CLI 前端（shared.runtime.frontend_main），但进程边界不变：

    FastAPI 进程 ── front_in  ──▶ 后端进程 ──▶ 进程1 / 进程2
                 ◀── front_out ──

multiprocessing.Queue 是阻塞 API，这里用一个后台线程把结果搬运到 asyncio 事件循环，
每个请求用 rid 关联各自的 Future，因此多个 WebSocket / HTTP 客户端可以并发使用。

接口：
- GET  /               内置一个可直接使用的聊天页面
- GET  /healthz        健康检查
- POST /chat           {"text": "...", "session_id": "..."} -> {"rid", "reply"}
- WS   /ws/chat        发送 {"text": "..."}，接收 start/delta/done/error 事件
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .protocol import ErrorResult, ReplyResult, UserRequest, new_rid


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except ValueError:
        return default


# 流式推送的粒度：worker 返回的是完整文本，这里在前端层分片下发；
# 若要真正的 token 级流式，需要进程2 通过队列发送增量事件（见 README）。
_CHUNK_SIZE = _int_env("WS_CHUNK_SIZE", 4)
_CHUNK_DELAY = _float_env("WS_CHUNK_DELAY", 0.02)


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    session_id: str = Field(default="http-default", max_length=128)


class ChatOut(BaseModel):
    rid: str
    reply: str


class ReplyBroker:
    """把阻塞的 multiprocessing.Queue 桥接到 asyncio。"""

    def __init__(self, request_queue: Any, response_queue: Any, timeout: float = 300.0) -> None:
        self._request_queue = request_queue
        self._response_queue = response_queue
        self._timeout = timeout
        self._pending: dict[str, asyncio.Future[ReplyResult]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._thread is not None:
            return
        self._loop = loop
        self._thread = threading.Thread(target=self._pump, name="reply-pump", daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        while True:
            try:
                response = self._response_queue.get()
            except (EOFError, OSError, ValueError):
                return
            if response is None:
                return
            loop = self._loop
            if loop is None or loop.is_closed():
                return
            try:
                loop.call_soon_threadsafe(self._dispatch, response)
            except RuntimeError:  # 事件循环正在关闭
                return

    def _dispatch(self, response: Any) -> None:
        future = self._pending.pop(getattr(response, "rid", ""), None)
        if future is None or future.done():
            return
        if isinstance(response, ErrorResult):
            future.set_exception(RuntimeError(response.error))
        elif isinstance(response, ReplyResult):
            future.set_result(response)
        else:
            future.set_exception(RuntimeError(f"未知响应：{response!r}"))

    async def ask(self, text: str, session_id: str) -> ReplyResult:
        rid = new_rid()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ReplyResult] = loop.create_future()
        self._pending[rid] = future
        self._request_queue.put(
            UserRequest(rid=rid, text=text, session_id=session_id or "default")
        )
        try:
            return await asyncio.wait_for(future, timeout=self._timeout)
        finally:
            self._pending.pop(rid, None)


def _parse_ws_message(raw: str) -> tuple[str, str | None]:
    """兼容纯文本和 {"text": ..., "session_id": ...} 两种消息格式。"""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip()[:2000], None

    if not isinstance(data, dict):
        return raw.strip()[:2000], None

    text = str(data.get("text", "")).strip()[:2000]
    session = data.get("session_id")
    session_id = str(session).strip()[:128] if session else None
    return text, session_id or None


def _chunks(text: str, size: int = _CHUNK_SIZE):
    for index in range(0, len(text), size):
        yield text[index : index + size]


async def _stream_reply(websocket: WebSocket, response: ReplyResult) -> None:
    await websocket.send_json({"type": "start", "rid": response.rid})
    for chunk in _chunks(response.reply):
        await websocket.send_json({"type": "delta", "content": chunk})
        await asyncio.sleep(_CHUNK_DELAY)
    await websocket.send_json(
        {"type": "done", "rid": response.rid, "reply": response.reply}
    )


def create_app(
    request_queue: Any,
    response_queue: Any,
    timeout: float = 300.0,
) -> FastAPI:
    broker = ReplyBroker(request_queue, response_queue, timeout=timeout)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        broker.start(asyncio.get_running_loop())
        yield

    app = FastAPI(
        title="情绪陪伴 · WebSocket 前端进程",
        version="1.1.0",
        lifespan=lifespan,
    )

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _INDEX_HTML

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/chat", response_model=ChatOut)
    async def chat(payload: ChatIn) -> ChatOut:
        text = payload.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="text 不能为空")
        try:
            response = await broker.ask(text, payload.session_id)
        except Exception as exc:  # 后端/子进程错误
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return ChatOut(rid=response.rid, reply=response.reply)

    @app.websocket("/ws/chat")
    async def ws_chat(websocket: WebSocket) -> None:
        await websocket.accept()
        session_id = websocket.query_params.get("session_id") or f"ws-{new_rid()[:8]}"
        await websocket.send_json({"type": "ready", "session_id": session_id})

        try:
            while True:
                raw = await websocket.receive_text()
                text, new_session = _parse_ws_message(raw)
                if new_session:
                    session_id = new_session

                if not text:
                    await websocket.send_json(
                        {"type": "error", "message": "消息不能为空"}
                    )
                    continue

                await websocket.send_json({"type": "thinking"})
                try:
                    response = await broker.ask(text, session_id)
                except Exception as exc:
                    await websocket.send_json({"type": "error", "message": str(exc)})
                    continue

                await _stream_reply(websocket, response)
        except (WebSocketDisconnect, RuntimeError):
            return

    return app


def web_frontend_main(
    request_queue: Any,
    response_queue: Any,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """前端进程入口：启动 uvicorn，退出时通知后端关闭。"""
    try:
        import uvicorn

        app = create_app(request_queue, response_queue)
        uvicorn.run(app, host=host, port=port, log_level="info")
    except ImportError:
        print(
            "缺少 fastapi / uvicorn，请先执行 pip install -r requirements.txt",
            file=sys.stderr,
            flush=True,
        )
        raise
    finally:
        request_queue.put(None)


_INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>情绪陪伴</title>
<style>
  :root { color-scheme: light dark; --bg:#f5f6f8; --panel:#fff; --text:#111827; --muted:#6b7280; --accent:#3b82f6; --user:#dbeafe; --bot:#f3f4f6; --border:#d1d5db; }
  @media (prefers-color-scheme: dark) { :root { --bg:#111827; --panel:#1f2937; --text:#f9fafb; --muted:#9ca3af; --accent:#60a5fa; --user:#1e3a8a; --bot:#374151; --border:#4b5563; } }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; background:var(--bg); color:var(--text); }
  .app { max-width:760px; margin:0 auto; height:100vh; display:flex; flex-direction:column; padding:16px; gap:12px; }
  header { display:flex; align-items:baseline; justify-content:space-between; }
  h1 { font-size:18px; margin:0; }
  #status { font-size:12px; color:var(--muted); }
  #messages { flex:1; overflow-y:auto; display:flex; flex-direction:column; gap:10px; padding:4px; }
  .msg { max-width:82%; padding:10px 14px; border-radius:14px; line-height:1.6; white-space:pre-wrap; word-break:break-word; font-size:15px; }
  .user { align-self:flex-end; background:var(--user); border-bottom-right-radius:4px; }
  .bot { align-self:flex-start; background:var(--bot); border-bottom-left-radius:4px; }
  .typing { color:var(--muted); font-style:italic; }
  form { display:flex; gap:8px; }
  input { flex:1; padding:12px 14px; border-radius:10px; border:1px solid var(--border); background:var(--panel); color:var(--text); font-size:15px; }
  input:focus { outline:2px solid var(--accent); outline-offset:1px; }
  button { padding:12px 20px; border:none; border-radius:10px; background:var(--accent); color:#fff; font-size:15px; cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; }
</style>
</head>
<body>
<div class="app">
  <header><h1>情绪陪伴</h1><span id="status">连接中…</span></header>
  <div id="messages"></div>
  <form id="form">
    <input id="input" autocomplete="off" placeholder="说点什么吧…" aria-label="输入内容">
    <button id="send" type="submit">发送</button>
  </form>
</div>
<script>
const statusEl = document.getElementById('status');
const messagesEl = document.getElementById('messages');
const form = document.getElementById('form');
const input = document.getElementById('input');
const sendBtn = document.getElementById('send');

let ws = null;
let currentBubble = null;
let busy = false;

function addMessage(role, text) {
  const el = document.createElement('div');
  el.className = 'msg ' + role;
  el.textContent = text;
  messagesEl.appendChild(el);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return el;
}

function setBusy(value) {
  busy = value;
  sendBtn.disabled = value;
  input.disabled = value;
  if (!value) input.focus();
}

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(proto + '://' + location.host + '/ws/chat');

  ws.onopen = () => { statusEl.textContent = '已连接'; };
  ws.onclose = () => { statusEl.textContent = '连接已断开'; setBusy(false); };
  ws.onerror = () => { statusEl.textContent = '连接出错'; };

  ws.onmessage = (event) => {
    let data;
    try { data = JSON.parse(event.data); } catch (error) { return; }

    if (data.type === 'ready') {
      statusEl.textContent = '已连接';
    } else if (data.type === 'thinking') {
      currentBubble = addMessage('bot', '正在感受你的情绪…');
      currentBubble.classList.add('typing');
    } else if (data.type === 'start') {
      if (!currentBubble) currentBubble = addMessage('bot', '');
      currentBubble.classList.remove('typing');
      currentBubble.textContent = '';
    } else if (data.type === 'delta') {
      if (!currentBubble) currentBubble = addMessage('bot', '');
      currentBubble.classList.remove('typing');
      currentBubble.textContent += data.content || '';
      messagesEl.scrollTop = messagesEl.scrollHeight;
    } else if (data.type === 'done') {
      if (!currentBubble) currentBubble = addMessage('bot', data.reply || '');
      else currentBubble.textContent = data.reply || currentBubble.textContent;
      currentBubble.classList.remove('typing');
      currentBubble = null;
      setBusy(false);
    } else if (data.type === 'error') {
      if (currentBubble) { currentBubble.remove(); currentBubble = null; }
      addMessage('bot', '出错了：' + (data.message || '未知错误'));
      setBusy(false);
    }
  };
}

form.addEventListener('submit', (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text || busy || !ws || ws.readyState !== WebSocket.OPEN) return;
  addMessage('user', text);
  input.value = '';
  setBusy(true);
  ws.send(JSON.stringify({ text: text }));
});

connect();
</script>
</body>
</html>
"""
