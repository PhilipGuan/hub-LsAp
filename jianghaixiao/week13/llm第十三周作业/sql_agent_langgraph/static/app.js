const messagesEl = document.getElementById("messages");
const composerEl = document.getElementById("composer");
const questionEl = document.getElementById("question");
const sendEl = document.getElementById("send");
const newChatEl = document.getElementById("new-chat");
const statusEl = document.getElementById("status");
const healthEl = document.getElementById("health");

const THREAD_KEY = "langgraph-sql-agent-thread";
let threadId = loadThreadId();
let busy = false;

function makeThreadId() {
  if (globalThis.crypto?.randomUUID) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function loadThreadId() {
  const existing = localStorage.getItem(THREAD_KEY);
  if (existing) return existing;
  const created = makeThreadId();
  localStorage.setItem(THREAD_KEY, created);
  return created;
}

function resetThreadId() {
  threadId = makeThreadId();
  localStorage.setItem(THREAD_KEY, threadId);
}

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function addMessage(role, text) {
  const article = document.createElement("article");
  article.className = `message ${role}`;

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = role === "user" ? "你" : "AI";

  const body = document.createElement("div");
  body.className = "message-body";

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;

  body.appendChild(bubble);
  article.append(avatar, body);
  messagesEl.appendChild(article);
  scrollToBottom();
  return { article, body, bubble };
}

function createAssistantShell() {
  const article = document.createElement("article");
  article.className = "message assistant";

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = "AI";

  const body = document.createElement("div");
  body.className = "message-body";

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = "正在分析问题…";

  const steps = document.createElement("div");
  steps.className = "steps";

  const details = document.createElement("details");
  details.className = "details";
  details.hidden = true;

  const summary = document.createElement("summary");
  summary.textContent = "查看执行过程、SQL 与查询结果";

  const detailsContent = document.createElement("div");
  detailsContent.className = "details-content";

  details.append(summary, detailsContent);
  body.append(bubble, steps, details);
  article.append(avatar, body);
  messagesEl.appendChild(article);
  scrollToBottom();

  return { article, bubble, steps, details, detailsContent, completed: false };
}

function setBusy(value) {
  busy = value;
  sendEl.disabled = value;
  questionEl.disabled = value;
  messagesEl.setAttribute("aria-busy", String(value));
  document.querySelectorAll(".example-button").forEach((button) => {
    button.disabled = value;
  });
  statusEl.textContent = value ? "Agent 运行中…" : "就绪";
}

function stepDetail(step) {
  if (step.node === "select_tables" && step.selected_tables?.length) {
    return `已选择：${step.selected_tables.join("、")}`;
  }
  if (step.node === "generate_sql") {
    return "SQL 已生成，准备执行";
  }
  if (step.node === "execute_sql") {
    if (step.success) return `执行成功，返回 ${step.row_count ?? 0} 行`;
    return "执行失败，进入修复流程";
  }
  if (step.node === "repair_sql") {
    return "已根据数据库报错生成修复 SQL";
  }
  if (step.node === "summarize_answer") {
    return "正在整理最终回答";
  }
  return "";
}

function addStep(shell, payload) {
  shell.steps.querySelectorAll(".step-item.active").forEach((item) => {
    item.classList.remove("active");
    item.classList.add("done");
  });

  const item = document.createElement("div");
  item.className = "step-item active";

  const dot = document.createElement("span");
  dot.className = "step-dot";

  const text = document.createElement("div");
  const label = document.createElement("span");
  label.textContent = payload.label || payload.node;
  text.appendChild(label);

  const detailText = stepDetail(payload);
  if (detailText) {
    const detail = document.createElement("span");
    detail.className = "step-detail";
    detail.textContent = detailText;
    text.appendChild(detail);
  }

  item.append(dot, text);
  shell.steps.appendChild(item);
  scrollToBottom();
}

function formatCell(value) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") {
    return Number.isInteger(value)
      ? value.toLocaleString("zh-CN")
      : value.toLocaleString("zh-CN", { maximumFractionDigits: 4 });
  }
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

function renderTable(container, payload) {
  const rows = Array.isArray(payload.rows) ? payload.rows : [];
  const columns = Array.isArray(payload.columns) && payload.columns.length
    ? payload.columns
    : (rows[0] ? Object.keys(rows[0]) : []);

  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = payload.sql_error ? "查询未成功执行。" : "查询成功，但没有匹配的数据。";
    container.appendChild(empty);
    return;
  }

  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  const table = document.createElement("table");

  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  columns.forEach((column) => {
    const th = document.createElement("th");
    th.textContent = column;
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);

  const tbody = document.createElement("tbody");
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    columns.forEach((column) => {
      const td = document.createElement("td");
      td.textContent = formatCell(row[column]);
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });

  table.append(thead, tbody);
  wrap.appendChild(table);
  container.appendChild(wrap);
}

function detailBlock(title, contentBuilder) {
  const block = document.createElement("section");
  block.className = "detail-block";
  const heading = document.createElement("h3");
  heading.textContent = title;
  block.appendChild(heading);
  contentBuilder(block);
  return block;
}

function renderResult(shell, payload) {
  shell.completed = true;
  shell.bubble.textContent = payload.answer || "Agent 没有返回答案。";
  shell.steps.querySelectorAll(".step-item.active").forEach((item) => {
    item.classList.remove("active");
    item.classList.add("done");
  });

  shell.detailsContent.replaceChildren();

  shell.detailsContent.appendChild(
    detailBlock("Agent 中间信息", (block) => {
      const meta = document.createElement("p");
      meta.className = "meta-line";
      const tables = payload.selected_tables?.length
        ? payload.selected_tables.join("、")
        : "无";
      meta.textContent = `选择的数据表：${tables}｜修复次数：${payload.retry_count ?? 0}`;
      if (payload.selection_reason) {
        meta.textContent += `｜选择原因：${payload.selection_reason}`;
      }
      block.appendChild(meta);
    })
  );

  if (payload.sql) {
    shell.detailsContent.appendChild(
      detailBlock("最终 SQL", (block) => {
        const pre = document.createElement("pre");
        pre.textContent = payload.sql;
        block.appendChild(pre);
      })
    );
  }

  if (payload.sql_error) {
    shell.detailsContent.appendChild(
      detailBlock("执行错误", (block) => {
        const error = document.createElement("p");
        error.className = "error-text";
        error.textContent = payload.sql_error;
        block.appendChild(error);
      })
    );
  }

  shell.detailsContent.appendChild(
    detailBlock("查询结果", (block) => renderTable(block, payload))
  );

  shell.details.hidden = false;
  shell.details.open = true;
  statusEl.textContent = "完成";
  scrollToBottom();
}

function handleSseBlock(rawBlock, shell) {
  let eventName = "message";
  const dataLines = [];

  for (const line of rawBlock.split("\n")) {
    if (line.startsWith("event:")) {
      eventName = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }

  if (!dataLines.length) return;
  const payload = JSON.parse(dataLines.join("\n"));

  if (eventName === "step") {
    addStep(shell, payload);
  } else if (eventName === "result") {
    renderResult(shell, payload);
  } else if (eventName === "error") {
    throw new Error(payload.message || "Agent 执行失败");
  }
}

async function streamChat(question, shell) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 180000);

  try {
    const response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: question, thread_id: threadId }),
      signal: controller.signal,
    });

    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `请求失败：HTTP ${response.status}`);
    }

    if (!response.body) {
      throw new Error("浏览器不支持流式响应");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      buffer = buffer.replace(/\r\n/g, "\n");

      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        if (block.trim()) handleSseBlock(block, shell);
        boundary = buffer.indexOf("\n\n");
      }
    }

    buffer += decoder.decode();
    if (buffer.trim()) handleSseBlock(buffer, shell);
    if (!shell.completed) throw new Error("连接结束，但未收到最终结果");
  } finally {
    clearTimeout(timer);
  }
}

async function sendQuestion(textFromExample) {
  if (busy) return;
  const question = (textFromExample ?? questionEl.value).trim();
  if (!question) return;

  addMessage("user", question);
  questionEl.value = "";
  questionEl.style.height = "auto";
  const shell = createAssistantShell();
  setBusy(true);

  try {
    await streamChat(question, shell);
  } catch (error) {
    shell.article.classList.add("error");
    shell.bubble.textContent = error.name === "AbortError"
      ? "请求超时，请稍后重试。"
      : error.message;
    shell.details.hidden = true;
    statusEl.textContent = "执行失败";
  } finally {
    setBusy(false);
    scrollToBottom();
  }
}

function resizeTextarea() {
  questionEl.style.height = "auto";
  questionEl.style.height = `${Math.min(questionEl.scrollHeight, 160)}px`;
}

composerEl.addEventListener("submit", (event) => {
  event.preventDefault();
  sendQuestion();
});

questionEl.addEventListener("input", resizeTextarea);
questionEl.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    sendQuestion();
  }
});

document.querySelectorAll(".example-button").forEach((button) => {
  button.addEventListener("click", () => sendQuestion(button.dataset.question || ""));
});

newChatEl.addEventListener("click", () => {
  if (busy) return;
  resetThreadId();
  messagesEl.replaceChildren();
  addMessage(
    "assistant",
    "已开始新对话。请继续询问 Chinook 数据库中的数据。"
  );
  questionEl.focus();
});

async function checkHealth() {
  try {
    const response = await fetch("/api/health");
    if (!response.ok) throw new Error("health check failed");
    const data = await response.json();
    if (data.llm_configured) {
      healthEl.textContent = `已连接 · ${data.model}`;
      healthEl.className = "health-pill ok";
    } else {
      healthEl.textContent = "未配置模型密钥";
      healthEl.className = "health-pill warn";
    }
  } catch {
    healthEl.textContent = "服务未连接";
    healthEl.className = "health-pill warn";
  }
}

checkHealth();
questionEl.focus();
