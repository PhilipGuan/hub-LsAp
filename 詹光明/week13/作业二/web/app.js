/* Chinook SQL 助手 —— 前端逻辑（无构建依赖，原生 DOM + fetch SSE） */
(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const chat = $("#chat");
  const welcome = $("#welcome");
  const examples = $("#examples");
  const composer = $("#composer");
  const input = $("#question");
  const sendButton = $("#send");
  const stopButton = $("#stop");
  const statusBox = $("#status");
  const statusText = $("#status-text");
  const backendNote = $("#backend-note");
  const dbName = $("#db-name");

  const state = {
    streaming: false,
    controller: null,
    config: null,
  };

  /* ------------------------------------------------------------ 工具 */

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[ch]);
  }

  function formatMs(ms) {
    if (ms == null) return "";
    if (ms < 1000) return `${ms} ms`;
    return `${(ms / 1000).toFixed(1)} s`;
  }

  function scrollToBottom() {
    chat.scrollTop = chat.scrollHeight;
  }

  async function readError(response) {
    try {
      const data = await response.json();
      if (typeof data.detail === "string") return data.detail;
      if (Array.isArray(data.detail)) {
        return data.detail.map((item) => item.msg || "参数错误").join("；");
      }
      return `请求失败（HTTP ${response.status}）`;
    } catch {
      return `请求失败（HTTP ${response.status}）`;
    }
  }

  async function copyText(text, button) {
    const original = button.textContent;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        const area = el("textarea");
        area.value = text;
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.append(area);
        area.select();
        document.execCommand("copy");
        area.remove();
      }
      button.textContent = "已复制";
    } catch {
      button.textContent = "复制失败";
    }
    window.setTimeout(() => { button.textContent = original; }, 1500);
  }

  /* ------------------------------------------------------------ Markdown 精简渲染 */

  function splitRow(line) {
    const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
    const cells = [];
    let current = "";
    for (let i = 0; i < trimmed.length; i += 1) {
      const ch = trimmed[i];
      if (ch === "\\" && trimmed[i + 1] === "|") {
        current += "|";
        i += 1;
      } else if (ch === "|") {
        cells.push(current);
        current = "";
      } else {
        current += ch;
      }
    }
    cells.push(current);
    return cells;
  }

  function replaceTables(source, stash) {
    const lines = source.split("\n");
    const out = [];
    const isRow = (line) => /^\s*\|.*\|\s*$/.test(line);
    const isSeparator = (line) => /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/.test(line)
      && line.includes("|");

    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i];
      const next = lines[i + 1];
      if (isRow(line) && next !== undefined && isSeparator(next)) {
        const rows = [line];
        let j = i + 2;
        while (j < lines.length && isRow(lines[j])) {
          rows.push(lines[j]);
          j += 1;
        }
        const head = splitRow(rows[0]).map((cell) => cell.trim());
        const body = rows.slice(1).map((row) => splitRow(row).map((c) => c.trim()));
        const table = [
          '<div class="table-wrap"><table><thead><tr>',
          head.map((cell) => `<th>${cell}</th>`).join(""),
          "</tr></thead><tbody>",
          body.map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join("")}</tr>`).join(""),
          "</tbody></table></div>",
        ].join("");
        out.push(stash(table));
        i = j - 1;
      } else {
        out.push(line);
      }
    }
    return out.join("\n");
  }

  /** 只支持标题外的常见格式：段落、加粗、行内代码、代码块、表格。 */
  function renderMarkdown(text) {
    const blocks = [];
    const stash = (block) => {
      blocks.push(block);
      return `\u0000${blocks.length - 1}\u0000`;
    };

    let source = escapeHtml(String(text ?? "").replace(/\r\n?/g, "\n"));
    source = source.replace(/```[^\n]*\n([\s\S]*?)```/g, (_, code) =>
      stash(`<pre class="code-block"><code>${code.replace(/\n$/, "")}</code></pre>`));
    source = replaceTables(source, stash);
    source = source
      .replace(/`([^`\n]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");

    const html = source
      .split(/\n{2,}/)
      .map((part) => (/^\u0000\d+\u0000$/.test(part.trim())
        ? part.trim()
        : `<p>${part.replace(/\n/g, "<br>")}</p>`))
      .join("");

    return html.replace(/\u0000(\d+)\u0000/g, (_, index) => blocks[Number(index)]);
  }

  /* ------------------------------------------------------------ 消息渲染 */

  function createStep(step, status) {
    const item = el("li", `step ${status}`);
    const icon = el("span", "icon");
    const body = el("span", "body");
    if (status === "pending") {
      icon.append(el("span", "spinner"));
    } else {
      icon.textContent = status === "error" ? "!" : "✓";
    }
    const label = el("span", "label", step.label || "");
    const detail = el("span", "detail", step.detail || "");
    body.append(label, detail);
    item.append(icon, body);
    return item;
  }

  function appendUserMessage(text) {
    const wrap = el("div", "msg user");
    const bubble = el("div", "bubble");
    bubble.textContent = text;
    wrap.append(bubble);
    chat.append(wrap);
    scrollToBottom();
  }

  function appendAssistantMessage() {
    const wrap = el("div", "msg assistant");
    const avatar = el("div", "avatar", "AI");
    avatar.setAttribute("aria-hidden", "true");
    const bubble = el("div", "bubble");
    const timeline = el("ol", "timeline");
    const pending = createStep({ label: "正在分析问题", detail: "选表 → 生成 SQL → 执行" }, "pending");
    timeline.append(pending);
    bubble.append(timeline);
    wrap.append(avatar, bubble);
    chat.append(wrap);
    scrollToBottom();
    return { bubble, timeline, pending, concluded: false };
  }

  function addProgress(ui, step) {
    if (ui.concluded) return;
    const item = createStep(step, step.status === "error" ? "error" : "done");
    if (ui.pending && ui.pending.isConnected) {
      ui.timeline.insertBefore(item, ui.pending);
    } else {
      ui.timeline.append(item);
    }
    scrollToBottom();
  }

  function removePending(ui) {
    if (ui.pending && ui.pending.isConnected) ui.pending.remove();
  }

  function addChips(ui, output, elapsedMs) {
    const chips = el("div", "chips");
    const add = (text, tone) => chips.append(el("span", `chip${tone ? ` ${tone}` : ""}`, text));

    if (output.tables && output.tables.length) add(`表：${output.tables.join("、")}`);
    if (output.sql) {
      add(`${output.row_count} 行`);
      if (output.truncated) add("结果已截断", "warn");
    }
    if (output.repair_attempts > 0) add(`改写 ${output.repair_attempts} 次`, "warn");
    if (elapsedMs != null) add(formatMs(elapsedMs));
    if (chips.childElementCount) ui.bubble.append(chips);
  }

  function addSection(ui, title, items, tone) {
    if (!items || !items.length) return;
    const section = el("section", `section${tone ? ` ${tone}` : ""}`);
    section.append(el("h3", null, title));
    const list = el("ul");
    items.forEach((item) => list.append(el("li", null, item)));
    section.append(list);
    ui.bubble.append(section);
  }

  function addSqlBlock(ui, sql) {
    const details = el("details");
    const summary = el("summary");
    const label = el("span", "summary-label", "查看 SQL");
    const copy = el("button", "copy", "复制");
    copy.type = "button";
    copy.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      copyText(sql, copy);
    });
    summary.append(label, copy);
    const pre = el("pre");
    pre.append(el("code", null, sql));
    details.append(summary, pre);
    ui.bubble.append(details);
  }

  function addRawBlock(ui, payload) {
    const details = el("details");
    const summary = el("summary");
    summary.append(el("span", "summary-label", "原始结果 JSON"));
    details.append(summary);
    const pre = el("pre");
    pre.textContent = JSON.stringify(payload, null, 2);
    details.append(pre);
    ui.bubble.append(details);
  }

  function showError(ui, message) {
    if (ui.concluded) return;
    const banner = el("div", "error-banner");
    banner.append(el("span", "mark", "!"), el("span", null, message));
    ui.bubble.append(banner);
    ui.concluded = true;
    removePending(ui);
    scrollToBottom();
  }

  function renderResult(ui, payload) {
    if (ui.concluded) return;
    ui.concluded = true;
    removePending(ui);

    const output = payload.output || {};
    const answer = el("div", "answer");
    answer.innerHTML = renderMarkdown(output.answer || "查询完成，但没有返回回答。");
    ui.bubble.append(answer);

    addChips(ui, output, payload.elapsed_ms);
    addSection(ui, "依据", output.evidence);
    addSection(ui, "注意", output.caveats, "caveats");

    if (output.error) {
      const banner = el("div", "error-banner");
      banner.append(el("span", "mark", "!"), el("span", null, output.error));
      ui.bubble.append(banner);
    }

    if (output.sql) addSqlBlock(ui, output.sql);
    addRawBlock(ui, payload);
    scrollToBottom();
  }

  /* ------------------------------------------------------------ 状态与初始化 */

  function setStatus(kind, text) {
    statusBox.dataset.state = kind;
    statusText.textContent = text;
  }

  async function loadHealth() {
    try {
      const response = await fetch("/api/health", { cache: "no-store" });
      const data = await response.json();
      if (data.status === "ok") {
        setStatus("ok", `已连接 · ${data.model}`);
      } else if (data.status === "missing_key") {
        setStatus("missing_key", "缺少 API Key");
        backendNote.hidden = false;
        backendNote.textContent =
          "后端尚未配置 OPENAI_API_KEY：在 week13/作业一/.env 或环境变量中填写后重启服务，即可开始查询。";
      } else if (data.status === "missing_db") {
        setStatus("missing_db", "数据库缺失");
        backendNote.hidden = false;
        backendNote.textContent = `找不到数据库 ${data.database}，请先在 week13/作业一 运行 python scripts/fetch_chinook.py。`;
      } else {
        setStatus("error", "后端异常");
        backendNote.hidden = false;
        backendNote.textContent = data.error || "后端初始化失败，请查看服务端日志。";
      }
    } catch {
      setStatus("error", "无法连接后端");
    }
  }

  function renderExamples(list) {
    examples.replaceChildren();
    (list || []).forEach((question) => {
      const button = el("button", "example", question);
      button.type = "button";
      button.addEventListener("click", () => {
        if (state.streaming) return;
        input.value = question;
        autoResize();
        composer.requestSubmit();
      });
      examples.append(button);
    });
  }

  async function loadConfig() {
    try {
      const response = await fetch("/api/config", { cache: "no-store" });
      const data = await response.json();
      state.config = data;
      renderExamples(data.examples);
      if (data.database) dbName.textContent = data.database;
      if (!data.api_key_configured) {
        backendNote.hidden = false;
        if (!backendNote.textContent) {
          backendNote.textContent =
            "后端尚未配置 OPENAI_API_KEY：请先在 week13/作业一 中配置，再重启本服务。";
        }
      }
    } catch {
      renderExamples(["销售额最高的前 5 位艺术家是谁？", "每个国家的客户数量，按数量降序"]);
    }
  }

  /* ------------------------------------------------------------ 流式请求 */

  function parseSSE(raw) {
    let event = "message";
    const dataLines = [];
    raw.split("\n").forEach((line) => {
      if (line.startsWith("event:")) {
        event = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).replace(/^ /, ""));
      }
    });
    if (!dataLines.length) return null;
    try {
      return { event, data: JSON.parse(dataLines.join("\n")) };
    } catch {
      return null;
    }
  }

  function handleEvent(event, ui) {
    if (!event) return;
    switch (event.event) {
      case "progress":
        addProgress(ui, event.data);
        break;
      case "result":
        renderResult(ui, event.data);
        break;
      case "error":
        showError(ui, event.data.message || "查询失败");
        break;
      case "done":
        removePending(ui);
        if (!ui.concluded) {
          showError(ui, "连接结束，但没有收到结果。");
        }
        break;
      default:
        break;
    }
  }

  async function consumeStream(response, ui) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let index = buffer.indexOf("\n\n");
      while (index !== -1) {
        const raw = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        handleEvent(parseSSE(raw), ui);
        index = buffer.indexOf("\n\n");
      }
    }
    if (buffer.trim()) handleEvent(parseSSE(buffer), ui);
  }

  async function askViaJson(question, ui) {
    const response = await fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
      signal: state.controller.signal,
    });
    if (!response.ok) throw new Error(await readError(response));
    const payload = await response.json();
    renderResult(ui, payload);
  }

  async function ask(question) {
    if (state.streaming || !question.trim()) return;

    state.streaming = true;
    state.controller = new AbortController();
    sendButton.disabled = true;
    stopButton.hidden = false;
    if (welcome) welcome.remove();

    appendUserMessage(question.trim());
    const ui = appendAssistantMessage();
    input.value = "";
    autoResize();

    try {
      const response = await fetch("/api/query/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: question.trim() }),
        signal: state.controller.signal,
      });

      if (!response.ok) throw new Error(await readError(response));

      if (!response.body) {
        await askViaJson(question.trim(), ui);
      } else {
        await consumeStream(response, ui);
        if (!ui.concluded) {
          showError(ui, "连接中断，未收到完整结果。");
        }
      }
    } catch (error) {
      if (error.name === "AbortError") {
        removePending(ui);
        ui.concluded = true;
        const note = el("div", "error-banner");
        note.append(el("span", "mark", "·"), el("span", null, "已停止本次查询。"));
        ui.bubble.append(note);
      } else {
        showError(ui, error.message || "网络错误，请稍后重试。");
      }
    } finally {
      state.streaming = false;
      state.controller = null;
      sendButton.disabled = false;
      stopButton.hidden = true;
      scrollToBottom();
      input.focus();
    }
  }

  /* ------------------------------------------------------------ 交互绑定 */

  function autoResize() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 164)}px`;
  }

  function bindEvents() {
    composer.addEventListener("submit", (event) => {
      event.preventDefault();
      ask(input.value);
    });

    input.addEventListener("input", autoResize);

    input.addEventListener("keydown", (event) => {
      // isComposing / keyCode 229：中文输入法选词时按 Enter 不发送
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
        event.preventDefault();
        composer.requestSubmit();
      }
    });

    stopButton.addEventListener("click", () => {
      if (state.controller) state.controller.abort();
    });
  }

  bindEvents();
  loadHealth();
  loadConfig();
})();
