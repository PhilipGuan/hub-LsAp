// 前端只与 FastAPI 交互，SSE 事件：emotion → token×N → done | error

const API_BASE = "http://localhost:8000";

const messagesEl = document.getElementById("messages");
const bannerEl = document.getElementById("banner");
const inputEl = document.getElementById("input");
const sendBtn = document.getElementById("send");
const statusEl = document.getElementById("status");

const state = {
  sending: false,
  conversationId: getOrCreateId("emotion-chat-conversation"),
  userId: getOrCreateId("emotion-chat-user"),
};

function getOrCreateId(key) {
  let value = localStorage.getItem(key);
  if (!value) {
    value =
      typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID().slice(0, 8)
        : Math.random().toString(36).slice(2, 10);
    localStorage.setItem(key, value);
  }
  return value;
}

function setStatus(text) {
  statusEl.textContent = text;
}

function addMessage(role, text) {
  const row = document.createElement("div");
  row.className = `row ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  row.appendChild(bubble);
  messagesEl.appendChild(row);
  scrollToBottom();
  return bubble;
}

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function showBanner(emotion) {
  if (emotion.label === "negative") {
    const intensity =
      emotion.intensity === "high" ? "较强" : emotion.intensity === "low" ? "较轻" : "一定";
    bannerEl.textContent = `检测到${intensity}的负面情绪，慢慢说，我在听。`;
    bannerEl.classList.remove("hidden");
  } else {
    bannerEl.classList.add("hidden");
  }
}

function parseEventBlock(block) {
  let event = "message";
  const dataLines = [];

  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).replace(/^ /, ""));
    }
  }

  if (dataLines.length === 0) return null;

  try {
    return { event, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return { event, data: { raw: dataLines.join("\n") } };
  }
}

async function sendMessage(text) {
  if (state.sending || !text.trim()) return;

  state.sending = true;
  sendBtn.disabled = true;
  setStatus("处理中…");
  bannerEl.classList.add("hidden");

  addMessage("user", text);
  const bubble = addMessage("assistant", "");
  bubble.classList.add("streaming");

  let accumulated = "";
  let fatalError = null;

  try {
    const response = await fetch(`${API_BASE}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        user_id: state.userId,
        conversation_id: state.conversationId,
      }),
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    if (!response.body) {
      throw new Error("当前浏览器不支持流式响应");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finished = false;

    while (!finished) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      let separator;
      while ((separator = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, separator);
        buffer = buffer.slice(separator + 2);

        const parsed = parseEventBlock(block);
        if (!parsed) continue;

        if (parsed.event === "emotion") {
          showBanner(parsed.data);
        } else if (parsed.event === "token") {
          accumulated += parsed.data.token || "";
          bubble.textContent = accumulated;
          scrollToBottom();
        } else if (parsed.event === "done") {
          accumulated = parsed.data.reply || accumulated;
          bubble.textContent = accumulated;
          setStatus("就绪");
          finished = true;
          break;
        } else if (parsed.event === "error") {
          throw new Error(parsed.data.message || "服务异常");
        }
      }
    }
  } catch (error) {
    fatalError = error;
  } finally {
    bubble.classList.remove("streaming");
    state.sending = false;
    sendBtn.disabled = false;

    if (fatalError) {
      bubble.classList.add("error");
      bubble.textContent = accumulated || `请求失败：${fatalError.message}`;
      setStatus("出错");
    } else if (!bubble.textContent) {
      bubble.textContent = "（无回复）";
      setStatus("就绪");
    }
  }
}

document.getElementById("composer").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = inputEl.value.trim();
  if (!text) return;
  inputEl.value = "";
  sendMessage(text);
});

inputEl.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    const text = inputEl.value.trim();
    if (!text || state.sending) return;
    inputEl.value = "";
    sendMessage(text);
  }
});
