// 前端逻辑：SSE 流式接收 → 逐字渲染 + 实时展示 Agent/工具/handoff 轨迹
const chatEl = document.getElementById("chat");
const inputEl = document.getElementById("input");
const sendEl = document.getElementById("send");

const MODE_TEXT = { encourage: "鼓励", comfort: "排解" };
const LABEL_TEXT = { positive: "正向", negative: "负面" };

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

// 创建 bot 消息容器（先显示等待动画，delta 到达后逐字填充）
function createBotMsg() {
  const wrap = document.createElement("div");
  wrap.className = "msg bot";

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML = '<span class="dots"><i></i><i></i><i></i></span>';

  const events = document.createElement("div");
  events.className = "events";

  wrap.appendChild(bubble);
  wrap.appendChild(events);
  chatEl.appendChild(wrap);
  scrollToBottom();

  return { wrap, bubble, events, text: "" };
}

function addChip(eventsEl, text, kind) {
  const chip = document.createElement("span");
  chip.className = "chip " + (kind || "");
  chip.textContent = text;
  eventsEl.appendChild(chip);
}

// 渲染识别标签与判断依据
function renderMeta(wrap, data) {
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
  if (meta.childNodes.length) wrap.appendChild(meta);
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

function parseSSE(raw) {
  let event = "message";
  const dataLines = [];
  raw.split("\n").forEach((line) => {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  });
  return { event, data: dataLines.join("\n") };
}

async function submit() {
  const text = inputEl.value.trim();
  if (!text || busy) return;

  addUserMsg(text);
  inputEl.value = "";
  setBusy(true);
  const ui = createBotMsg();

  try {
    const resp = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    });

    // 参数错误/后端故障：返回的是普通 JSON
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({ error: `HTTP ${resp.status}` }));
      ui.wrap.remove();
      addErrorMsg(data.error || `HTTP ${resp.status}`);
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finished = false;
    let gotDelta = false;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let idx;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const raw = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const { event, data } = parseSSE(raw);
        if (!data) continue;

        let payload;
        try {
          payload = JSON.parse(data);
        } catch (e) {
          continue;
        }

        if (event === "agent") {
          addChip(ui.events, "Agent: " + payload.name, "agent");
        } else if (event === "tool") {
          addChip(ui.events, "工具: " + payload.name, "tool");
        } else if (event === "handoff") {
          addChip(ui.events, "→ " + payload.to, "handoff");
        } else if (event === "delta") {
          if (!gotDelta) {
            ui.bubble.textContent = "";
            gotDelta = true;
          }
          ui.text += payload.text || "";
          ui.bubble.textContent = ui.text;
          scrollToBottom();
        } else if (event === "done") {
          finished = true;
          // 兜底分支没有流式增量，用最终结果补全
          if (!ui.text && payload.reply) {
            ui.bubble.textContent = payload.reply;
            ui.text = payload.reply;
          }
          renderMeta(ui.wrap, payload);
        } else if (event === "error") {
          finished = true;
          ui.wrap.classList.add("error");
          ui.bubble.textContent = "出错了：" + (payload.error || "未知错误");
        }
        scrollToBottom();
      }
    }

    if (!finished) {
      ui.wrap.classList.add("error");
      ui.bubble.textContent = "出错了：连接中断，未收到完整结果";
    }
  } catch (err) {
    ui.wrap.classList.add("error");
    ui.bubble.textContent = "出错了：无法连接服务（" + err.message + "）";
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
