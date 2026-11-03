/* SQL Agent 对话前端：fetch + ReadableStream 解析 SSE，
   实时渲染 LangGraph 节点流水线与最终结果。 */

"use strict";

const STAGE_LABELS = {
  select_tables: "选表",
  generate_sql: "生成 SQL",
  validate_sql: "校验",
  execute_sql: "执行",
  rewrite_sql: "改写",
  summarize: "汇总",
  finalize: "完成",
};

const chat = document.getElementById("chat");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const healthEl = document.getElementById("health");

let streaming = false;
let rewriteCount = 0;

/* ---------- 健康检查 ---------- */

async function checkHealth() {
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    if (data.api_key_configured) {
      healthEl.textContent = "● 服务正常";
      healthEl.className = "health ok";
      healthEl.title = `模型: ${data.model} · 数据库: ${data.db}`;
    } else {
      healthEl.textContent = "● 缺少 API key";
      healthEl.className = "health bad";
      healthEl.title = "服务端未配置 OPENAI_API_KEY";
    }
  } catch {
    healthEl.textContent = "● 服务不可用";
    healthEl.className = "health bad";
  }
}

/* ---------- 简易 Markdown 渲染（表格 / 加粗 / 行内代码 / 换行） ---------- */

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[ch]);
}

function renderMarkdown(text) {
  const lines = escapeHtml(text || "").split("\n");
  const out = [];
  let table = null;

  const flushTable = () => {
    if (!table) return;
    const [head, ...rows] = table;
    let html = "<table><thead><tr>";
    head.forEach((cell) => { html += `<th>${cell.trim()}</th>`; });
    html += "</tr></thead><tbody>";
    rows.filter((r) => !/^\s*:?-{2,}/.test(r.join(""))).forEach((row) => {
      html += "<tr>";
      row.forEach((cell) => { html += `<td>${cell.trim()}</td>`; });
      html += "</tr>";
    });
    html += "</tbody></table>";
    out.push(html);
    table = null;
  };

  for (const line of lines) {
    if (line.trim().startsWith("|")) {
      if (!table) table = [];
      table.push(line.trim().replace(/^\||\|$/g, "").split("|"));
      continue;
    }
    flushTable();
    let html = line
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
    out.push(html);
  }
  flushTable();
  return out.join("\n");
}

/* ---------- 聊天消息 ---------- */

function scrollBottom() {
  chat.scrollTop = chat.scrollHeight;
}

function addUserMessage(text) {
  const div = document.createElement("div");
  div.className = "msg user";
  div.innerHTML = `<div class="avatar">我</div><div class="bubble"></div>`;
  div.querySelector(".bubble").textContent = text;
  chat.appendChild(div);
  scrollBottom();
}

function newAssistantMessage() {
  const div = document.createElement("div");
  div.className = "msg assistant";
  div.innerHTML = `
    <div class="avatar">◆</div>
    <div class="bubble">
      <div class="pipeline"></div>
      <div class="result"></div>
    </div>`;
  chat.appendChild(div);
  scrollBottom();
  return {
    root: div,
    pipeline: div.querySelector(".pipeline"),
    result: div.querySelector(".result"),
  };
}

/* ---------- 流水线 ---------- */

function addStage(msg, node, state, detail) {
  // finalize 由 final 事件渲染成"完成"，避免重复
  if (node === "finalize") return;
  const pending = msg.pipeline.querySelector(".stage.pending");
  if (pending) pending.remove();

  const label = node === "rewrite_sql"
    ? `${STAGE_LABELS[node]}（第 ${++rewriteCount} 次）`
    : STAGE_LABELS[node] || node;
  const chip = document.createElement("div");
  chip.className = `stage ${state}`;
  chip.innerHTML = `<span class="dot"></span><span class="label"></span><span class="detail"></span>`;
  chip.querySelector(".label").textContent = label;
  chip.querySelector(".detail").textContent = detail || "";
  msg.pipeline.appendChild(chip);
  scrollBottom();
}

function addPendingStage(msg) {
  const chip = document.createElement("div");
  chip.className = "stage pending";
  chip.innerHTML = `<span class="dot"></span><span class="label">运行中…</span>`;
  msg.pipeline.appendChild(chip);
}

/* ---------- 节点事件 -> 流水线明细 ---------- */

function nodeDetail(node, info) {
  const trunc = (s, n = 80) => (s && s.length > n ? s.slice(0, n) + "…" : s || "");
  switch (node) {
    case "select_tables":
      return info.needs_clarification
        ? "需要澄清"
        : `选定 ${JSON.stringify(info.tables)}（置信度 ${(info.confidence ?? 0).toFixed(2)}）`;
    case "generate_sql":
      return trunc(info.sql);
    case "validate_sql":
      return info.ok ? "校验通过" : `未通过：${info.code || ""} ${trunc(info.message, 60)}`;
    case "execute_sql":
      return info.ok
        ? `返回 ${info.row_count} 行${info.truncated ? "（已截断）" : ""}`
        : `失败：${info.code || ""} ${trunc(info.message, 60)}`;
    case "rewrite_sql":
      return info.cannot_fix ? "无法修复" : trunc(info.sql);
    case "summarize":
      return trunc(info.answer, 60);
    default:
      return "";
  }
}

/* ---------- 最终结果 ---------- */

function renderFinal(msg, output) {
  const answerDiv = document.createElement("div");
  answerDiv.className = "answer";
  answerDiv.innerHTML = renderMarkdown(output.answer);
  msg.result.appendChild(answerDiv);

  if (output.caveats && output.caveats.length) {
    const ul = document.createElement("ul");
    ul.className = "caveats";
    output.caveats.forEach((c) => { ul.innerHTML += `<li>⚠ ${escapeHtml(c)}</li>`; });
    msg.result.appendChild(ul);
  }
  if (output.evidence && output.evidence.length) {
    const ul = document.createElement("ul");
    ul.className = "evidence";
    ul.innerHTML = `<li>依据：${output.evidence.map(escapeHtml).join("；")}</li>`;
    msg.result.appendChild(ul);
  }

  const details = document.createElement("details");
  details.className = "meta";
  const stats = `表: ${JSON.stringify(output.tables)} · 行数: ${output.row_count}` +
    `${output.truncated ? "（截断）" : ""} · 修复: ${output.repair_attempts} 次`;
  details.innerHTML = `
    <summary>执行明细（${stats}）</summary>
    ${output.sql ? `<div class="kv"><b>SQL：</b></div><pre>${escapeHtml(output.sql)}</pre>` : ""}
    <div class="kv"><b>原始 JSON：</b></div>
    <pre>${escapeHtml(JSON.stringify(output, null, 2))}</pre>`;
  msg.result.appendChild(details);
  scrollBottom();
}

/* ---------- SSE 读取 ---------- */

async function streamQuery(question) {
  const msg = newAssistantMessage();
  addPendingStage(msg);
  rewriteCount = 0;

  let finalOutput = null;
  let errorMessage = null;

  try {
    const res = await fetch("/api/query/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    if (!res.ok || !res.body) {
      const text = await res.text().catch(() => "");
      throw new Error(`HTTP ${res.status} ${text.slice(0, 200)}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let idx;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const event = parseSseBlock(block);
        if (!event) continue;

        if (event.name === "node") {
          addStage(msg, event.data.node, "done", nodeDetail(event.data.node, event.data.info));
        } else if (event.name === "final") {
          finalOutput = event.data.output;
        } else if (event.name === "error") {
          errorMessage = event.data.message;
        }
      }
    }
  } catch (err) {
    errorMessage = `请求失败：${err.message}`;
  }

  if (finalOutput) {
    renderFinal(msg, finalOutput);
    const failed = Boolean(finalOutput.error);
    addStage(msg, "finalize", failed ? "error" : "done",
      failed ? finalOutput.error : "查询完成");
  } else {
    addStage(msg, "finalize", "error", errorMessage || "未知错误");
    msg.result.innerHTML = `<div class="answer">处理失败：${escapeHtml(errorMessage || "")}</div>`;
  }
  enableInput();
}

function parseSseBlock(block) {
  let name = "message";
  const dataLines = [];
  block.split("\n").forEach((line) => {
    if (line.startsWith("event:")) name = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  });
  if (!dataLines.length) return null;
  try {
    return { name, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return null;
  }
}

/* ---------- 输入控制 ---------- */

function enableInput() {
  streaming = false;
  sendBtn.disabled = false;
  input.disabled = false;
  input.focus();
}

async function send() {
  const question = input.value.trim();
  if (!question || streaming) return;
  streaming = true;
  sendBtn.disabled = true;
  input.disabled = true;
  input.value = "";
  document.querySelector(".welcome")?.remove();
  addUserMessage(question);
  await streamQuery(question);
}

sendBtn.addEventListener("click", send);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    send();
  }
});
document.querySelectorAll(".example").forEach((btn) => {
  btn.addEventListener("click", () => {
    input.value = btn.dataset.q;
    send();
  });
});
input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 140) + "px";
});

checkHealth();
input.focus();
