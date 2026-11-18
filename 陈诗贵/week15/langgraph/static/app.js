// 前端逻辑：提交用户输入 → 调同源 /api/chat（由前端进程转发给后端）→ 渲染回复
const chatEl = document.getElementById("chat");
const inputEl = document.getElementById("input");
const sendEl = document.getElementById("send");

const MODE_TEXT = { encourage: "鼓励", comfort: "排解" };
const LABEL_TEXT = { positive: "正向", negative: "负面" };
const NODE_TEXT = {
  node_sentiment: "情感识别",
  node_encourage: "鼓励",
  node_comfort: "排解",
};

let busy = false;

function scrollToBottom() {
  chatEl.scrollTop = chatEl.scrollHeight;
}

function clearWelcome() {
  const welcome = chatEl.querySelector(".welcome");
  if (welcome) welcome.remove();
}

function addUserMsg(text) {
  clearWelcome();
  const wrap = document.createElement("div");
  wrap.className = "msg user";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  wrap.appendChild(bubble);
  chatEl.appendChild(wrap);
  scrollToBottom();
}

function addLoading() {
  const wrap = document.createElement("div");
  wrap.className = "msg bot";
  wrap.id = "loading";
  wrap.innerHTML = '<div class="bubble"><span class="dots"><i></i><i></i><i></i></span></div>';
  chatEl.appendChild(wrap);
  scrollToBottom();
}

function removeLoading() {
  const el = document.getElementById("loading");
  if (el) el.remove();
}

function buildMeta(data) {
  const meta = document.createElement("div");
  meta.className = "meta";

  const sentiment = data.sentiment || {};
  if (sentiment.label) {
    const tag = document.createElement("span");
    tag.className = "tag " + (sentiment.label === "positive" ? "positive" : "negative");
    const score = typeof sentiment.score === "number" ? ` ${Math.round(sentiment.score * 100)}%` : "";
    tag.textContent = (LABEL_TEXT[sentiment.label] || sentiment.label) + score;
    meta.appendChild(tag);
  }

  if (data.mode) {
    const mode = document.createElement("span");
    mode.className = "tag mode";
    mode.textContent = MODE_TEXT[data.mode] || data.mode;
    meta.appendChild(mode);
  }

  if (sentiment.reason) {
    const reason = document.createElement("span");
    reason.className = "reason";
    reason.textContent = sentiment.reason;
    meta.appendChild(reason);
  }

  if (Array.isArray(data.trace) && data.trace.length) {
    const trace = document.createElement("span");
    trace.className = "trace";
    trace.textContent = data.trace.map((n) => NODE_TEXT[n] || n).join(" → ");
    meta.appendChild(trace);
  }

  return meta;
}

function addBotMsg(data) {
  const wrap = document.createElement("div");
  wrap.className = "msg bot";

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = data.reply || "（没有生成回复）";
  wrap.appendChild(bubble);

  const meta = buildMeta(data);
  if (meta.childNodes.length) wrap.appendChild(meta);

  chatEl.appendChild(wrap);
  scrollToBottom();
}

function addErrorMsg(message) {
  const wrap = document.createElement("div");
  wrap.className = "msg bot error";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = "出错了：" + message;
  wrap.appendChild(bubble);
  chatEl.appendChild(wrap);
  scrollToBottom();
}

function setBusy(value) {
  busy = value;
  sendEl.disabled = value || !inputEl.value.trim();
  sendEl.textContent = value ? "识别中…" : "发送";
}

async function submit() {
  const text = inputEl.value.trim();
  if (!text || busy) return;

  addUserMsg(text);
  inputEl.value = "";
  setBusy(true);
  addLoading();

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    });
    const data = await resp.json().catch(() => ({ error: "返回内容不是合法 JSON" }));
    removeLoading();

    if (!resp.ok || data.error) {
      addErrorMsg(data.error || `HTTP ${resp.status}`);
    } else {
      addBotMsg(data);
    }
  } catch (err) {
    removeLoading();
    addErrorMsg("无法连接服务，请确认后端进程已启动（" + err.message + "）");
  } finally {
    setBusy(false);
    inputEl.focus();
  }
}

inputEl.addEventListener("input", () => {
  if (!busy) sendEl.disabled = !inputEl.value.trim();
});

inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    submit();
  }
});

sendEl.addEventListener("click", submit);

document.querySelectorAll(".sample").forEach((btn) => {
  btn.addEventListener("click", () => {
    inputEl.value = btn.textContent;
    submit();
  });
});
