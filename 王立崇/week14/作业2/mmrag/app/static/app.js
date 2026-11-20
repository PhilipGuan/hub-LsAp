(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const state = {
    config: null,
    health: null,
    docs: [],
    stats: {},
    selectedDocs: new Set(),
    messages: [],
    loading: false,
    loadingTimer: null,
    chatImage: null,
    uploading: false,
    docsLoading: false,
    refreshTimer: null,
    eventSource: null,
    sessionId: (crypto.randomUUID ? crypto.randomUUID() : String(Date.now())),
  };

  const dom = {
    messages: $("#messages"),
    thread: $("#thread"),
    welcome: $("#welcome"),
    docList: $("#doc-list"),
    docEmpty: $("#doc-empty"),
    statDocs: $("#stat-docs"),
    statChunks: $("#stat-chunks"),
    statImages: $("#stat-images"),
    healthPill: $("#health-pill"),
    healthLabel: $("#health-label"),
    fileInput: $("#file-input"),
    dropzone: $("#dropzone"),
    tierSelect: $("#tier-select"),
    maxSizeLabel: $("#max-size-label"),
    uploadProgress: $("#upload-progress"),
    uploadProgressLabel: $("#upload-progress-label"),
    uploadProgressValue: $("#upload-progress-value"),
    uploadProgressBar: $("#upload-progress-bar"),
    refreshDocs: $("#refresh-docs"),
    scopePill: $("#scope-pill"),
    scopeLabel: $("#scope-label"),
    scopeBar: $("#scope-bar"),
    clearChat: $("#clear-chat"),
    question: $("#question"),
    send: $("#send"),
    attachImage: $("#attach-image"),
    chatImageInput: $("#chat-image-input"),
    imagePreview: $("#image-preview"),
    imagePreviewImg: $("#image-preview-img"),
    removeImage: $("#remove-image"),
    suggestions: $("#suggestions"),
    topbarSubtitle: $("#topbar-subtitle"),
    lightbox: $("#lightbox"),
    lightboxImg: $("#lightbox-img"),
    lightboxCaption: $("#lightbox-caption"),
    docModal: $("#doc-modal"),
    previewTitle: $("#preview-title"),
    previewMeta: $("#preview-meta"),
    previewImages: $("#preview-images"),
    previewMarkdown: $("#preview-markdown"),
    toast: $("#toast"),
  };

  const STATUS_META = {
    pending: { label: "待解析", className: "status-pending" },
    parsing: { label: "解析中", className: "status-parsing" },
    completed: { label: "解析完成", className: "status-completed" },
    failed: { label: "解析失败", className: "status-failed" },
  };

  const KIND_LABEL = { text: "正文", table: "表格", image: "插图" };

  const icons = {
    trash: '<svg viewBox="0 0 24 24" fill="none"><path d="M5 7h14M10 7V5h4v2m-7 0 1 12h8l1-12" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    copy: '<svg viewBox="0 0 24 24" fill="none"><rect x="8" y="8" width="11" height="11" rx="2" stroke="currentColor" stroke-width="1.6"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" stroke="currentColor" stroke-width="1.6"/></svg>',
    spark: '<svg viewBox="0 0 24 24" fill="none"><path d="m12 3 1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3Z" stroke="currentColor" stroke-width="1.55" stroke-linejoin="round"/><path d="m18.5 16 .8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8.8-2.2Z" fill="currentColor"/></svg>',
  };

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[char]));
  }

  function escapeAttr(value) {
    return escapeHtml(value).replace(/\n/g, " ");
  }

  function formatNumber(value) {
    return new Intl.NumberFormat("zh-CN").format(Number(value) || 0);
  }

  function formatTime(value) {
    const date = value ? new Date(value) : new Date();
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  }

  function safeImageUrl(raw) {
    const value = String(raw || "").trim();
    if (/^\/media\/[^\s"'<>]+$/.test(value)) return value;
    if (/^data:image\/(?:png|jpe?g|webp|gif);base64,[a-z0-9+/=]+$/i.test(value)) return value;
    return "";
  }

  function safeLinkUrl(raw) {
    const value = String(raw || "").trim();
    if (/^(?:https?:\/\/|\/|#)/i.test(value)) return value;
    return "";
  }

  /* ============================== Markdown =============================== */
  function renderInline(raw) {
    // The local model occasionally emits <br> inside table cells. Treat only
    // that tag as a line break; every other HTML tag is escaped below.
    let text = String(raw).replace(/<br\s*\/?>/gi, "\n");
    text = escapeHtml(text);
    const codeSpans = [];
    text = text.replace(/`([^`\n]+)`/g, (_, code) => {
      const token = `\u0000code${codeSpans.length}\u0000`;
      codeSpans.push(`<code>${code}</code>`);
      return token;
    });

    text = text.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (_, alt, url) => {
      const safe = safeImageUrl(url);
      if (!safe) return alt;
      return `<img class="md-image" src="${escapeAttr(safe)}" alt="${alt}" loading="lazy" data-lightbox="1" data-caption="${alt}">`;
    });

    text = text.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, url) => {
      const safe = safeLinkUrl(url);
      if (!safe) return label;
      const external = /^https?:\/\//i.test(safe);
      return `<a href="${escapeAttr(safe)}"${external ? ' target="_blank" rel="noopener noreferrer"' : ""}>${label}</a>`;
    });

    text = text.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    text = text.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
    text = text.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
    text = text.replace(/\[(\d{1,2})\]/g, '<span class="cite-pill">$1</span>');
    text = text.replace(/\n/g, "<br>");

    codeSpans.forEach((html, index) => {
      text = text.replace(`\u0000code${index}\u0000`, html);
    });
    return text;
  }

  function isTableSeparator(line) {
    const cells = splitTableRow(line);
    return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell.trim()));
  }

  function splitTableRow(line) {
    let value = line.trim();
    if (value.startsWith("|")) value = value.slice(1);
    if (value.endsWith("|")) value = value.slice(0, -1);
    return value.split("|").map((cell) => cell.trim());
  }

  function renderTable(lines) {
    const header = splitTableRow(lines[0]);
    const body = lines.slice(2).map(splitTableRow).filter((row) => row.length);
    const head = header.map((cell) => `<th>${renderInline(cell)}</th>`).join("");
    const rows = body.map((row) => {
      const cells = header.map((_, index) => `<td>${renderInline(row[index] || "")}</td>`).join("");
      return `<tr>${cells}</tr>`;
    }).join("");
    return `<table><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>`;
  }

  function startsBlock(line) {
    return !line.trim() ||
      /^#{1,6}\s+/.test(line) ||
      /^```/.test(line) ||
      /^\s*>\s?/.test(line) ||
      /^\s*(?:[-*+]|\d+\.)\s+/.test(line) ||
      /^\s*(?:---|___|\*\*\*)\s*$/.test(line) ||
      (line.includes("|") && line.trim().startsWith("|"));
  }

  function renderMarkdown(source) {
    if (!source) return "";
    const lines = String(source).replace(/\r\n?/g, "\n").split("\n");
    const html = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i += 1; continue; }

      const fence = line.match(/^```([\w+-]*)\s*$/);
      if (fence) {
        i += 1;
        const code = [];
        while (i < lines.length && !/^```\s*$/.test(lines[i])) {
          code.push(lines[i]);
          i += 1;
        }
        if (i < lines.length) i += 1;
        html.push(`<pre><code class="language-${escapeAttr(fence[1] || "")}">${escapeHtml(code.join("\n"))}</code></pre>`);
        continue;
      }

      const heading = line.match(/^(#{1,6})\s+(.+)$/);
      if (heading) {
        const level = heading[1].length;
        html.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
        i += 1;
        continue;
      }

      if (/^\s*(?:---|___|\*\*\*)\s*$/.test(line)) {
        html.push("<hr>");
        i += 1;
        continue;
      }

      if (/^\s*>\s?/.test(line)) {
        const quote = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
          quote.push(lines[i].replace(/^\s*>\s?/, ""));
          i += 1;
        }
        html.push(`<blockquote>${quote.map(renderInline).join("<br>")}</blockquote>`);
        continue;
      }

      if (line.includes("|") && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
        const tableLines = [line, lines[i + 1]];
        i += 2;
        while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
          tableLines.push(lines[i]);
          i += 1;
        }
        html.push(renderTable(tableLines));
        continue;
      }

      const listMatch = line.match(/^\s*((?:[-*+])|(?:\d+\.))\s+(.+)$/);
      if (listMatch) {
        const ordered = /\d+\./.test(listMatch[1]);
        const tag = ordered ? "ol" : "ul";
        const items = [];
        while (i < lines.length) {
          const item = lines[i].match(/^\s*((?:[-*+])|(?:\d+\.))\s+(.+)$/);
          if (!item) break;
          items.push(`<li>${renderInline(item[2])}</li>`);
          i += 1;
        }
        html.push(`<${tag}>${items.join("")}</${tag}>`);
        continue;
      }

      const paragraph = [];
      while (i < lines.length && lines[i].trim() && !startsBlock(lines[i])) {
        paragraph.push(lines[i]);
        i += 1;
      }
      if (paragraph.length) {
        html.push(`<p>${paragraph.map(renderInline).join("<br>")}</p>`);
      } else {
        i += 1;
      }
    }
    return html.join("\n");
  }

  /* ============================== API utils ============================== */
  function formatApiError(detail) {
    if (!detail) return "请求失败";
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail.map((item) => item.msg || JSON.stringify(item)).join("；");
    }
    if (typeof detail === "object") {
      if (detail.message) return String(detail.message);
      return JSON.stringify(detail);
    }
    return String(detail);
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: options.body ? { "Content-Type": "application/json", ...(options.headers || {}) } : options.headers,
      ...options,
    });
    let data = null;
    try { data = await response.json(); } catch (_) { /* noop */ }
    if (!response.ok) {
      const detail = data && data.detail !== undefined ? data.detail : `HTTP ${response.status}`;
      throw new Error(formatApiError(detail));
    }
    return data;
  }

  function toast(message, variant = "") {
    dom.toast.textContent = message;
    dom.toast.className = `toast ${variant ? `is-${variant}` : ""}`;
    dom.toast.hidden = false;
    requestAnimationFrame(() => dom.toast.classList.add("is-visible"));
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => {
      dom.toast.classList.remove("is-visible");
      setTimeout(() => { dom.toast.hidden = true; }, 220);
    }, 3800);
  }

  /* ============================== Documents ============================== */
  function statusMeta(status) {
    return STATUS_META[status] || { label: status || "未知", className: "status-pending" };
  }

  function docSubtitle(doc) {
    if (doc.status === "completed") {
      return `${formatNumber(doc.page_count)} 页 · ${formatNumber(doc.chunk_count)} 个切片 · ${formatNumber(doc.image_count)} 张图像`;
    }
    if (doc.status === "parsing") {
      return `${escapeHtml(doc.stage || "MinerU 正在解析")} · ${Math.max(0, Math.min(100, doc.progress || 0))}%`;
    }
    if (doc.status === "failed") {
      return `<span class="failed-text">${escapeHtml((doc.error || "解析失败").slice(0, 72))}</span>`;
    }
    return `${escapeHtml(doc.stage || "等待 Kafka 调度")} · ${formatNumber(doc.size)} B`;
  }

  function renderDocs() {
    const docs = state.docs;
    dom.docEmpty.hidden = docs.length > 0;
    if (!docs.length) {
      dom.docList.innerHTML = "";
      renderScope();
      return;
    }

    dom.docList.innerHTML = docs.map((doc) => {
      const meta = statusMeta(doc.status);
      const selected = state.selectedDocs.has(doc.id);
      const canSelect = doc.status === "completed";
      const progress = Math.max(0, Math.min(100, Number(doc.progress) || 0));
      const showProgress = doc.status === "pending" || doc.status === "parsing";
      return `
        <div class="doc-item ${selected ? "is-selected" : ""}" data-doc-id="${escapeAttr(doc.id)}">
          <input class="doc-select" type="checkbox" ${selected ? "checked" : ""} ${canSelect ? "" : "disabled"} aria-label="选择 ${escapeAttr(doc.filename)} 作为检索范围">
          <div class="doc-main" data-open-doc="${escapeAttr(doc.id)}" title="${escapeAttr(doc.filename)}">
            <div class="doc-top">
              <span class="doc-name">${escapeHtml(doc.filename)}</span>
              <span class="status-badge ${meta.className}">${meta.label}</span>
            </div>
            <div class="doc-sub">${docSubtitle(doc)}</div>
            ${showProgress ? `<div class="doc-progress"><span style="width:${progress}%"></span></div>` : ""}
          </div>
          <button class="doc-delete" type="button" data-delete-doc="${escapeAttr(doc.id)}" title="删除文档" aria-label="删除 ${escapeAttr(doc.filename)}">${icons.trash}</button>
        </div>`;
    }).join("");
    renderScope();
  }

  function renderStats() {
    const stats = state.stats || {};
    dom.statDocs.textContent = formatNumber(stats.completed || 0);
    dom.statChunks.textContent = formatNumber(stats.chunks || 0);
    dom.statImages.textContent = formatNumber(stats.images || 0);
    if ((stats.completed || 0) > 0) {
      dom.topbarSubtitle.textContent = `${formatNumber(stats.completed)} 篇文档 · ${formatNumber(stats.chunks)} 个图文切片已就绪`;
    } else {
      dom.topbarSubtitle.textContent = "把 PDF 变成可以看图提问的知识库";
    }
  }

  function renderScope() {
    const selected = state.docs.filter((doc) => state.selectedDocs.has(doc.id));
    if (!selected.length) {
      state.selectedDocs.clear();
      dom.scopeLabel.textContent = "全部文档";
      dom.scopePill.classList.remove("is-scoped");
      dom.scopeBar.hidden = true;
      dom.scopeBar.innerHTML = "";
      return;
    }
    dom.scopeLabel.textContent = `聚焦 ${selected.length} 篇`;
    dom.scopePill.classList.add("is-scoped");
    dom.scopeBar.hidden = false;
    dom.scopeBar.innerHTML = `<span class="scope-bar-label">检索范围</span>${selected.map((doc) => `
      <span class="scope-chip">
        <span title="${escapeAttr(doc.filename)}">${escapeHtml(doc.filename)}</span>
        <button type="button" data-remove-scope="${escapeAttr(doc.id)}" aria-label="移除范围">×</button>
      </span>`).join("")}`;
  }

  async function loadDocs() {
    if (state.docsLoading) return;
    state.docsLoading = true;
    try {
      const data = await api("/api/documents");
      state.docs = data.documents || [];
      state.stats = data.stats || {};
      for (const id of Array.from(state.selectedDocs)) {
        const doc = state.docs.find((item) => item.id === id);
        if (!doc || doc.status !== "completed") state.selectedDocs.delete(id);
      }
      renderDocs();
      renderStats();
    } catch (error) {
      console.warn(error);
    } finally {
      state.docsLoading = false;
    }
  }

  function scheduleDocsRefresh(delay = 180) {
    clearTimeout(state.refreshTimer);
    state.refreshTimer = setTimeout(loadDocs, delay);
  }

  function applyDocumentEvent(doc) {
    if (!doc || !doc.id) return;
    const index = state.docs.findIndex((item) => item.id === doc.id);
    if (index >= 0) state.docs[index] = { ...state.docs[index], ...doc };
    else state.docs.unshift(doc);
    if (doc.status === "completed" || doc.status === "failed") {
      scheduleDocsRefresh(260);
    } else {
      renderDocs();
    }
  }

  function updateScopeSelection(id, checked) {
    if (checked) state.selectedDocs.add(id);
    else state.selectedDocs.delete(id);
    renderDocs();
  }

  async function deleteDoc(id) {
    const doc = state.docs.find((item) => item.id === id);
    if (!doc) return;
    if (!window.confirm(`删除《${doc.filename}》及其解析结果？`)) return;
    try {
      await api(`/api/documents/${encodeURIComponent(id)}`, { method: "DELETE" });
      state.selectedDocs.delete(id);
      await loadDocs();
      toast("文档已删除");
    } catch (error) {
      toast(error.message, "error");
    }
  }

  /* ================================ Upload ================================ */
  function setUploadProgress(percent, label) {
    dom.uploadProgress.hidden = false;
    dom.uploadProgressLabel.textContent = label;
    dom.uploadProgressValue.textContent = `${Math.round(percent)}%`;
    dom.uploadProgressBar.style.width = `${Math.max(1, Math.min(100, percent))}%`;
  }

  function uploadFiles(fileList) {
    const files = Array.from(fileList || []);
    const pdfs = files.filter((file) => file.name.toLowerCase().endsWith(".pdf") || file.type === "application/pdf");
    if (!pdfs.length) {
      toast("请选择 PDF 文件", "warn");
      return;
    }
    if (pdfs.length > 20) {
      toast("单次最多上传 20 个 PDF", "warn");
      return;
    }
    if (state.uploading) {
      toast("正在上传，请稍候", "warn");
      return;
    }

    const form = new FormData();
    pdfs.forEach((file) => form.append("files", file, file.name));
    form.append("tier", dom.tierSelect.value || "basic");
    state.uploading = true;
    setUploadProgress(1, `上传 ${pdfs.length} 个 PDF`);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload");
    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) return;
      setUploadProgress((event.loaded / event.total) * 100, `上传 ${pdfs.length} 个 PDF`);
    };
    xhr.onload = async () => {
      state.uploading = false;
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch (_) { /* noop */ }
      if (xhr.status >= 200 && xhr.status < 300) {
        setUploadProgress(100, "已提交 Kafka 解析队列");
        setTimeout(() => { dom.uploadProgress.hidden = true; }, 900);
        const rejected = data && data.rejected ? data.rejected : [];
        toast(`已提交 ${data.accepted || pdfs.length} 个解析任务`);
        if (rejected.length) {
          setTimeout(() => toast(`${rejected.length} 个文件被拒绝：${rejected[0].error}`, "warn"), 450);
        }
        await loadDocs();
        scheduleDocsRefresh(800);
      } else {
        dom.uploadProgress.hidden = true;
        const detail = data && data.detail !== undefined ? data.detail : `HTTP ${xhr.status}`;
        toast(formatApiError(detail), "error");
      }
      dom.fileInput.value = "";
    };
    xhr.onerror = () => {
      state.uploading = false;
      dom.uploadProgress.hidden = true;
      toast("上传失败，请检查服务是否正常", "error");
      dom.fileInput.value = "";
    };
    xhr.send(form);
  }

  /* ================================ Health ================================ */
  function renderHealth() {
    const health = state.health;
    if (!health) return;
    const kafkaOk = Boolean(health.kafka && health.kafka.ready);
    const llmOk = Boolean(health.llm && health.llm.ready);
    const mineruOk = Boolean(health.mineru && health.mineru.ready);
    let label = "服务正常";
    let cls = "is-ok";
    if (!kafkaOk) { label = "Kafka 未连接"; cls = "is-bad"; }
    else if (!llmOk) { label = "模型未连接"; cls = "is-bad"; }
    else if (!mineruOk) { label = "MinerU 未安装"; cls = "is-warn"; }
    dom.healthLabel.textContent = label;
    dom.healthPill.className = `health-pill ${cls}`;
    dom.healthPill.title = [
      `Kafka: ${kafkaOk ? "已连接" : "不可用"} (${health.kafka?.bootstrap || ""})`,
      `LLM: ${llmOk ? "已连接" : "不可用"} (${health.llm?.model || ""})`,
      `MinerU: ${mineruOk ? "可用" : "不可用"}`,
      health.llm?.detail ? `模型信息: ${String(health.llm.detail).slice(0, 180)}` : "",
    ].filter(Boolean).join("\n");
  }

  async function loadHealth() {
    try {
      state.health = await api("/api/health");
    } catch (error) {
      state.health = { kafka: { ready: false }, llm: { ready: false }, mineru: { ready: false } };
    }
    renderHealth();
  }

  /* ================================= Chat ================================= */
  function renderReferences(references) {
    if (!references || !references.length) return "";
    const cards = references.map((ref) => {
      const thumb = ref.image_url
        ? `<img class="source-thumb" src="${escapeAttr(ref.image_url)}" alt="${escapeAttr(ref.caption || ref.doc_name)}" loading="lazy" data-lightbox="1" data-caption="${escapeAttr(`${ref.doc_name} · 第 ${ref.page} 页`)}">`
        : "";
      return `
        <button class="source-card" type="button" data-open-doc="${escapeAttr(ref.doc_id)}" title="打开文档预览">
          ${thumb}
          <span class="source-card-body">
            <span class="source-card-top">
              <span class="source-num">${ref.n}</span>
              <span class="source-kind">${escapeHtml(ref.kind_label || KIND_LABEL[ref.kind] || "资料")}</span>
            </span>
            <span class="source-title">${escapeHtml(ref.doc_name)}</span>
            <span class="source-snippet">${escapeHtml(ref.snippet || ref.content || "")}</span>
            <span class="source-page">第 ${formatNumber(ref.page)} 页</span>
          </span>
        </button>`;
    }).join("");
    return `
      <details class="sources">
        <summary>引用来源 <span class="source-count">${references.length}</span></summary>
        <div class="source-grid">${cards}</div>
      </details>`;
  }

  function renderMessages() {
    dom.welcome.hidden = state.messages.length > 0 || state.loading;
    dom.messages.classList.toggle("is-chatting", state.messages.length > 0 || state.loading);
    const html = state.messages.map((message, index) => {
      if (message.role === "user") {
        return `
          <article class="message user">
            <div class="message-body">
              <div class="message-bubble">
                ${escapeHtml(message.content).replace(/\n/g, "<br>")}
                ${message.imageDataUrl ? `<div class="user-image-wrap"><img src="${escapeAttr(message.imageDataUrl)}" alt="用户上传图片" data-lightbox="1" data-caption="用户上传的图片"></div>` : ""}
              </div>
              <div class="message-time" style="text-align:right">${formatTime(message.at)}</div>
            </div>
            <div class="message-avatar">你</div>
          </article>`;
      }
      if (message.error) {
        return `
          <article class="message assistant error">
            <div class="message-avatar">${icons.spark}</div>
            <div class="message-body">
              <div class="message-meta"><strong>DocuMind</strong><span>请求失败</span></div>
              <div class="message-bubble">${escapeHtml(message.content)}</div>
            </div>
          </article>`;
      }
      const latency = message.meta?.latency_ms ? `${(message.meta.latency_ms / 1000).toFixed(1)}s` : "";
      return `
        <article class="message assistant">
          <div class="message-avatar">${icons.spark}</div>
          <div class="message-body">
            <div class="message-meta"><strong>DocuMind</strong><span>多模态 RAG${latency ? ` · ${latency}` : ""}</span></div>
            <div class="message-bubble markdown">${renderMarkdown(message.content)}</div>
            ${renderReferences(message.references)}
            <div class="answer-tools">
              <button class="answer-tool" type="button" data-copy-index="${index}">${icons.copy}<span>复制回答</span></button>
            </div>
          </div>
        </article>`;
    }).join("");

    const loading = state.loading ? `
      <article class="message assistant">
        <div class="message-avatar">${icons.spark}</div>
        <div class="message-body">
          <div class="message-meta"><strong>DocuMind</strong><span>正在检索</span></div>
          <div class="message-bubble loading-bubble">
            <div class="loading-dots"><span></span><span></span><span></span></div>
            <span class="loading-text">${escapeHtml(state.loadingStage || "正在检索图文资料…")}</span>
          </div>
        </div>
      </article>` : "";

    dom.thread.innerHTML = html + loading;
    if (state.loading) {
      requestAnimationFrame(() => {
        dom.messages.scrollTop = dom.messages.scrollHeight;
      });
    }
  }

  function scrollToBottom() {
    requestAnimationFrame(() => {
      dom.messages.scrollTop = dom.messages.scrollHeight;
    });
  }

  function resizeTextarea() {
    dom.question.style.height = "auto";
    dom.question.style.height = `${Math.min(dom.question.scrollHeight, 180)}px`;
  }

  function updateImagePreview() {
    if (state.chatImage) {
      dom.imagePreview.hidden = false;
      dom.imagePreviewImg.src = state.chatImage.dataUrl;
    } else {
      dom.imagePreview.hidden = true;
      dom.imagePreviewImg.removeAttribute("src");
    }
  }

  async function fileToDataUrl(file) {
    if (!file.type.startsWith("image/")) throw new Error("请选择图片文件");
    if (file.size > 20 * 1024 * 1024) throw new Error("原始图片不能超过 20 MB");
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height));
    const width = Math.max(1, Math.round(bitmap.width * scale));
    const height = Math.max(1, Math.round(bitmap.height * scale));
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    context.fillStyle = "#fff";
    context.fillRect(0, 0, width, height);
    context.drawImage(bitmap, 0, 0, width, height);
    bitmap.close?.();
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.86));
    if (!blob) throw new Error("图片处理失败");
    const dataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(new Error("图片读取失败"));
      reader.readAsDataURL(blob);
    });
    return dataUrl;
  }

  async function attachImageFile(file) {
    try {
      const dataUrl = await fileToDataUrl(file);
      state.chatImage = { dataUrl, name: file.name || "粘贴的图片" };
      updateImagePreview();
    } catch (error) {
      toast(error.message, "warn");
    }
  }

  async function sendQuestion(questionOverride = null) {
    const question = (questionOverride ?? dom.question.value).trim();
    if (state.loading) return;
    if (!question) {
      toast("先输入一个问题吧");
      dom.question.focus();
      return;
    }

    const image = state.chatImage;
    const history = state.messages
      .filter((message) => !message.error && message.content)
      .slice(-8)
      .map((message) => ({ role: message.role, content: message.content.slice(0, 4000) }));

    state.messages.push({
      role: "user",
      content: question,
      imageDataUrl: image ? image.dataUrl : null,
      at: Date.now(),
    });
    state.chatImage = null;
    updateImagePreview();
    dom.question.value = "";
    resizeTextarea();
    state.loading = true;
    state.loadingStage = "正在检索文档中的文字与图像…";
    renderMessages();
    dom.messages.scrollTop = dom.messages.scrollHeight;
    clearTimeout(state.loadingTimer);
    state.loadingTimer = setTimeout(() => {
      const el = $(".loading-text");
      if (el) el.textContent = "模型正在组织图文回答…";
    }, 2600);

    try {
      const data = await api("/api/chat", {
        method: "POST",
        body: JSON.stringify({
          question,
          image_data_url: image ? image.dataUrl : null,
          doc_ids: state.selectedDocs.size ? Array.from(state.selectedDocs) : null,
          history,
          session_id: state.sessionId,
        }),
      });
      state.messages.push({
        role: "assistant",
        content: data.answer || "模型没有返回内容。",
        references: data.references || [],
        meta: { latency_ms: data.latency_ms, model: data.model, tokens: data.query_tokens },
        at: Date.now(),
      });
    } catch (error) {
      state.messages.push({
        role: "assistant",
        error: true,
        content: `这次回答没有生成成功：${error.message}`,
      });
    } finally {
      clearTimeout(state.loadingTimer);
      state.loading = false;
      state.loadingStage = "";
      renderMessages();
      scrollToBottom();
      dom.question.focus();
    }
  }

  function clearChat() {
    if (!state.messages.length && !state.loading) return;
    state.messages = [];
    state.loading = false;
    clearTimeout(state.loadingTimer);
    dom.thread.innerHTML = "";
    dom.welcome.hidden = false;
    dom.messages.classList.remove("is-chatting");
    dom.messages.scrollTop = 0;
  }

  /* ================================ Preview =============================== */
  function openLightbox(src, caption) {
    const safe = safeImageUrl(src);
    if (!safe) return;
    dom.lightboxImg.src = safe;
    dom.lightboxImg.alt = caption || "图片预览";
    dom.lightboxCaption.textContent = caption || "";
    dom.lightbox.hidden = false;
  }

  function closeModal(modal) {
    modal.hidden = true;
    if (modal === dom.lightbox) {
      dom.lightboxImg.removeAttribute("src");
    }
  }

  async function openDocument(id) {
    const doc = state.docs.find((item) => item.id === id);
    if (doc && doc.status !== "completed") {
      toast(doc.status === "failed" ? "该文档解析失败，暂无可预览内容" : "文档仍在解析中，请稍后查看", "warn");
      return;
    }
    try {
      const detail = await api(`/api/documents/${encodeURIComponent(id)}`);
      dom.previewTitle.textContent = detail.title || detail.filename;
      dom.previewMeta.textContent = `${detail.filename} · ${formatNumber(detail.page_count)} 页 · ${formatNumber(detail.chunk_count)} 个切片 · ${formatNumber(detail.image_count)} 张图像 · MinerU ${detail.tier || "basic"}`;
      const images = detail.images || [];
      dom.previewImages.innerHTML = images.map((image) => {
        const url = image.image_url || `/media/${image.image_path}`;
        return `<img src="${escapeAttr(url)}" alt="${escapeAttr(image.caption || "文档图像")}" loading="lazy" data-lightbox="1" data-caption="${escapeAttr(`${detail.filename} · 第 ${image.page} 页`)}">`;
      }).join("");
      dom.previewMarkdown.innerHTML = detail.excerpt
        ? renderMarkdown(detail.excerpt)
        : '<p class="muted">暂无 Markdown 预览。</p>';
      dom.docModal.hidden = false;
    } catch (error) {
      toast(error.message, "error");
    }
  }

  /* ================================ Events ================================ */
  function connectEvents() {
    if (state.eventSource) state.eventSource.close();
    const source = new EventSource("/api/events");
    state.eventSource = source;
    source.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload.type === "document.status" && payload.document) {
          applyDocumentEvent(payload.document);
        }
      } catch (_) { /* keep alive comments are ignored */ }
    };
    source.onerror = () => {
      // EventSource retries automatically; polling remains the fallback.
    };
  }

  /* ================================ Wiring ================================ */
  function bindEvents() {
    dom.fileInput.addEventListener("change", (event) => uploadFiles(event.target.files));
    dom.dropzone.addEventListener("dragover", (event) => {
      event.preventDefault();
      dom.dropzone.classList.add("is-dragging");
    });
    dom.dropzone.addEventListener("dragleave", () => dom.dropzone.classList.remove("is-dragging"));
    dom.dropzone.addEventListener("drop", (event) => {
      event.preventDefault();
      dom.dropzone.classList.remove("is-dragging");
      uploadFiles(event.dataTransfer.files);
    });

    dom.refreshDocs.addEventListener("click", async () => {
      dom.refreshDocs.classList.add("is-spinning");
      await loadDocs();
      setTimeout(() => dom.refreshDocs.classList.remove("is-spinning"), 420);
    });

    dom.docList.addEventListener("click", (event) => {
      const deleteButton = event.target.closest("[data-delete-doc]");
      if (deleteButton) {
        event.stopPropagation();
        deleteDoc(deleteButton.dataset.deleteDoc);
        return;
      }
      const main = event.target.closest("[data-open-doc]");
      if (main) openDocument(main.dataset.openDoc);
    });
    dom.docList.addEventListener("change", (event) => {
      const checkbox = event.target.closest(".doc-select");
      if (!checkbox) return;
      const item = checkbox.closest("[data-doc-id]");
      if (item) updateScopeSelection(item.dataset.docId, checkbox.checked);
    });

    dom.scopePill.addEventListener("click", () => {
      if (state.selectedDocs.size) {
        state.selectedDocs.clear();
        renderDocs();
        toast("检索范围已切换为全部文档");
      } else {
        toast("在左侧勾选“解析完成”的文档，可以限定检索范围");
      }
    });
    dom.scopeBar.addEventListener("click", (event) => {
      const button = event.target.closest("[data-remove-scope]");
      if (!button) return;
      state.selectedDocs.delete(button.dataset.removeScope);
      renderDocs();
    });

    dom.clearChat.addEventListener("click", clearChat);
    dom.question.addEventListener("input", resizeTextarea);
    dom.question.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        sendQuestion();
      }
    });
    dom.question.addEventListener("paste", (event) => {
      const item = Array.from(event.clipboardData?.items || []).find((entry) => entry.type.startsWith("image/"));
      if (!item) return;
      const file = item.getAsFile();
      if (file) {
        event.preventDefault();
        attachImageFile(file);
      }
    });
    dom.send.addEventListener("click", () => sendQuestion());
    dom.attachImage.addEventListener("click", () => dom.chatImageInput.click());
    dom.chatImageInput.addEventListener("change", (event) => {
      const file = event.target.files?.[0];
      if (file) attachImageFile(file);
      event.target.value = "";
    });
    dom.removeImage.addEventListener("click", () => {
      state.chatImage = null;
      updateImagePreview();
      dom.question.focus();
    });

    dom.suggestions.addEventListener("click", (event) => {
      const button = event.target.closest("[data-question]");
      if (!button) return;
      sendQuestion(button.dataset.question);
    });

    dom.thread.addEventListener("click", async (event) => {
      const image = event.target.closest("[data-lightbox]");
      if (image) {
        event.preventDefault();
        event.stopPropagation();
        openLightbox(image.getAttribute("src"), image.dataset.caption || image.alt || "");
        return;
      }
      const openDoc = event.target.closest("[data-open-doc]");
      if (openDoc) {
        openDocument(openDoc.dataset.openDoc);
        return;
      }
      const copy = event.target.closest("[data-copy-index]");
      if (copy) {
        const index = Number(copy.dataset.copyIndex);
        const message = state.messages[index];
        if (!message) return;
        try {
          await navigator.clipboard.writeText(message.content || "");
          toast("回答已复制");
        } catch (_) {
          toast("复制失败，请手动选择文本", "warn");
        }
      }
    });

    dom.previewImages.addEventListener("click", (event) => {
      const image = event.target.closest("[data-lightbox]");
      if (image) openLightbox(image.getAttribute("src"), image.dataset.caption || "");
    });
    dom.previewMarkdown.addEventListener("click", (event) => {
      const image = event.target.closest("[data-lightbox]");
      if (image) {
        event.preventDefault();
        openLightbox(image.getAttribute("src"), image.dataset.caption || image.alt || "");
      }
    });

    $$("[data-close]").forEach((button) => {
      button.addEventListener("click", () => closeModal($(`#${button.dataset.close}`)));
    });
    [dom.lightbox, dom.docModal].forEach((modal) => {
      modal.addEventListener("click", (event) => {
        if (event.target === modal) closeModal(modal);
      });
    });
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      if (!dom.lightbox.hidden) closeModal(dom.lightbox);
      else if (!dom.docModal.hidden) closeModal(dom.docModal);
    });
  }

  async function loadConfig() {
    try {
      state.config = await api("/api/config");
      const maxSize = state.config.max_document_mb || 100;
      dom.maxSizeLabel.textContent = `${maxSize} MB`;
      if (state.config.default_tier) dom.tierSelect.value = state.config.default_tier;
      if (state.config.max_chat_image_mb) {
        dom.imagePreview.title = `单张图片最多 ${state.config.max_chat_image_mb} MB`;
      }
    } catch (_) { /* defaults are fine */ }
  }

  async function init() {
    bindEvents();
    resizeTextarea();
    await loadConfig();
    await Promise.all([loadDocs(), loadHealth()]);
    renderMessages();
    connectEvents();

    setInterval(() => {
      if (state.docs.some((doc) => doc.status === "pending" || doc.status === "parsing")) {
        loadDocs();
      }
    }, 4200);
    setInterval(loadHealth, 30000);
    dom.question.focus();
  }

  window.addEventListener("DOMContentLoaded", init);
})();
