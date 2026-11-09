// 对话前端：每条问题独立 POST /api/query，结果渲染成聊天气泡。
// 对话历史仅在前端维护，用于展示。

const chat = document.getElementById("chat");
const form = document.getElementById("composer-form");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const statusEl = document.getElementById("status");

const EXAMPLES = [
  "总共有多少张专辑？",
  "销量最高的 5 首曲目是什么？",
  "每个员工负责多少客户？",
  "哪个流派的歌曲数量最多？",
];

// ---------- 工具 ----------
const escapeHtml = (s) =>
  String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

// 极简 markdown：把 answer 里的 markdown 表格、代码块、**粗体**、换行渲染出来。
function renderMarkdown(text) {
  if (!text) return "";
  const lines = escapeHtml(text).split("\n");
  let html = "";
  let i = 0;
  let inTable = false;
  const flushTable = () => {
    if (inTable) { html += "</tbody></table>"; inTable = false; }
  };

  while (i < lines.length) {
    const line = lines[i];

    // 表格：连续的 | a | b | 行，第二行是分隔符 |---|---|
    if (/^\s*\|.+\|\s*$/.test(line)) {
      const isSeparator = /^\s*\|[\s:|-]+\|\s*$/.test(line);
      if (isSeparator) { i++; continue; }
      if (!inTable) { html += "<table><tbody>"; inTable = true; }
      const cells = line.trim().slice(1, -1).split("|").map((c) => c.trim());
      // 第一行作为表头
      const tag = i > 0 && /^\s*\|.+\|\s*$/.test(lines[i - 1]) === false && false ? "td" : "td";
      html +=
        "<tr>" +
        cells.map((c) => `<td>${c}</td>`).join("") +
        "</tr>";
      i++;
      // 表格结束判断
      if (!lines[i] || !/^\s*\|.+\|\s*$/.test(lines[i])) flushTable();
      continue;
    }
    flushTable();

    // 代码块
    if (line.trim().startsWith("```")) {
      const code = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        code.push(lines[i]);
        i++;
      }
      i++; // 跳过结束 ```
      html += `<pre class="sql">${code.join("\n")}</pre>`;
      continue;
    }

    // 空行
    if (line.trim() === "") { html += "<br>"; i++; continue; }

    // 普通行：粗体
    html += "<p>" + line.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>") + "</p>";
    i++;
  }
  flushTable();
  return html;
}

function autoGrow() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 160) + "px";
  sendBtn.disabled = input.value.trim().length === 0;
}

function scrollBottom() {
  chat.scrollTop = chat.scrollHeight;
}

function setStatus(state, text) {
  statusEl.dataset.state = state;
  statusEl.querySelector(".status-text").textContent = text;
}

// ---------- 渲染消息 ----------
function addUserMessage(text) {
  const node = document.getElementById("tpl-user").content.cloneNode(true);
  node.querySelector("p").textContent = text;
  chat.appendChild(node);
  scrollBottom();
}

function addAssistantPlaceholder() {
  const node = document.getElementById("tpl-assistant").content.cloneNode(true);
  const bubble = node.querySelector(".bubble");
  bubble.querySelector(".answer").innerHTML =
    '<div class="typing"><span></span><span></span><span></span></div>';
  bubble.querySelector(".meta").remove();
  chat.appendChild(node);
  scrollBottom();
  return chat.lastElementChild;
}

function renderAssistant(node, data) {
  const bubble = node.querySelector(".bubble");
  const answerEl = bubble.querySelector(".answer");
  answerEl.innerHTML = renderMarkdown(data.answer || "（无回答）");

  // 没有任何详情字段时隐藏 details
  const hasMeta =
    data.sql || (data.tables && data.tables.length) ||
    data.row_count || data.truncated || data.repair_attempts ||
    (data.evidence && data.evidence.length) ||
    (data.caveats && data.caveats.length) || data.error;

  if (!hasMeta) return;

  const meta = document.createElement("details");
  meta.className = "meta";
  meta.innerHTML = '<summary>查询详情</summary><div class="meta-body"></div>';
  const body = meta.querySelector(".meta-body");
  bubble.appendChild(meta);

  if (data.error) {
    const e = document.createElement("div");
    e.className = "error-note";
    e.textContent = "错误：" + data.error;
    body.appendChild(e);
  }

  if (data.sql) {
    const pre = document.createElement("pre");
    pre.className = "sql";
    pre.textContent = data.sql;
    body.appendChild(pre);
  }

  const chipsRow = document.createElement("div");
  chipsRow.className = "row";
  if (data.tables && data.tables.length) {
    chipsRow.innerHTML = '<span class="label">使用的表</span><span class="chips"></span>';
    const chips = chipsRow.querySelector(".chips");
    data.tables.forEach((t) => {
      const c = document.createElement("span");
      c.className = "chip";
      c.textContent = t;
      chips.appendChild(c);
    });
  }
  const stats = [];
  if (data.row_count) stats.push(`返回 ${data.row_count} 行`);
  if (data.truncated) stats.push("已截断");
  if (data.repair_attempts) stats.push(`修复 ${data.repair_attempts} 次`);
  if (stats.length) {
    const s = document.createElement("span");
    s.className = "breadcrumb";
    s.textContent = stats.join(" · ");
    chipsRow.appendChild(s);
  }
  if (chipsRow.childNodes.length) body.appendChild(chipsRow);

  if (data.evidence && data.evidence.length) {
    const ul = document.createElement("ul");
    ul.className = "bullets";
    data.evidence.forEach((ev) => {
      const li = document.createElement("li");
      li.textContent = ev;
      ul.appendChild(li);
    });
    body.appendChild(ul);
  }
  if (data.caveats && data.caveats.length) {
    const ul = document.createElement("ul");
    ul.className = "bullets caveats";
    data.caveats.forEach((c) => {
      const li = document.createElement("li");
      li.textContent = c;
      ul.appendChild(li);
    });
    body.appendChild(ul);
  }
}

// ---------- 发送 ----------
async function sendQuestion(question) {
  addUserMessage(question);
  const placeholder = addAssistantPlaceholder();

  try {
    const resp = await fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });

    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try { detail = (await resp.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    const data = await resp.json();
    renderAssistant(placeholder, data);
  } catch (err) {
    renderAssistant(placeholder, {
      answer: "查询失败：" + err.message,
      error: err.message,
    });
  } finally {
    scrollBottom();
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const q = input.value.trim();
  if (!q) return;
  input.value = "";
  autoGrow();
  sendQuestion(q);
});

input.addEventListener("input", autoGrow);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});

// ---------- 初始化：欢迎语 + 示例 ----------
function initWelcome() {
  const node = document.getElementById("tpl-assistant").content.cloneNode(true);
  const bubble = node.querySelector(".bubble");
  bubble.querySelector(".answer").innerHTML =
    "<p>你好，我是 Chinook 数据库查询助手。用自然语言提问，我会自动选表、生成并执行 SQL，再用一句话回答你。</p>" +
    '<div class="examples"></div>';
  bubble.querySelector(".meta").remove();
  const ex = bubble.querySelector(".examples");
  EXAMPLES.forEach((q) => {
    const b = document.createElement("button");
    b.className = "example-btn";
    b.type = "button";
    b.textContent = q;
    b.addEventListener("click", () => {
      input.value = q;
      autoGrow();
      form.requestSubmit();
    });
    ex.appendChild(b);
  });
  chat.appendChild(node);
  scrollBottom();
}

async function initHealth() {
  try {
    const r = await fetch("/health");
    const d = await r.json();
    if (d.api_key_configured) setStatus("ok", `就绪 · ${d.model}`);
    else setStatus("error", "未配置 API Key");
  } catch (_) {
    setStatus("error", "服务不可用");
  }
}

initWelcome();
initHealth();
autoGrow();
input.focus();
