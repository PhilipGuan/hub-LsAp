/* ==========================================================================
 * 多模态 RAG 文档问答 · 前端逻辑（layui）
 * --------------------------------------------------------------------------
 * 说明：
 *  - 上传走 layui.upload，多选后逐个文件上传，后端立即返回、解析异步进行；
 *  - 文档状态每 2 秒轮询一次（见 startPolling），状态来自 /api/documents；
 *  - 问答接口返回已渲染好的 HTML（含内嵌图片），这里经过白名单清洗后插入；
 *  - 会话 ID 存在 localStorage，刷新页面能恢复历史对话。
 *
 * 安全约定：本文件不直接写入 HTML 字符串。服务端返回的 HTML 先经
 * Range#createContextualFragment 解析（脚本不会执行），再移除危险标签与
 * on* 事件属性，最后用 replaceChildren 插入。
 * ========================================================================== */
(function () {
  "use strict";

  var state = {
    docs: [],
    selected: new Set(),
    sessionId: localStorage.getItem("rag.session") || null,
    image: null,
    sending: false,
    busyUpload: 0,
    maxUpload: 60,
    config: null,
    health: null
  };

  var layer = null;
  var upload = null;

  // ------------------------------ 启动 ----------------------------------
  layui.use(["layer", "upload"], function () {
    layer = layui.layer;
    upload = layui.upload;
    init();
  });

  async function init() {
    bindEvents();
    await Promise.allSettled([loadConfig(), loadHealth(), loadDocs(true)]);
    setupUpload();
    await loadHistory();
    startPolling();
    scrollBottom();
  }

  // ------------------------------ 基础工具 ------------------------------
  function $(id) {
    return document.querySelector("#" + id);
  }

  async function api(path, options) {
    options = options || {};
    var init = { method: options.method || "GET", headers: {} };
    if (options.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
    var response = await fetch(path, init);
    var data = null;
    try {
      data = await response.json();
    } catch (err) {
      data = null;
    }
    if (!response.ok) {
      var detail = data && (data.detail || data.message);
      throw new Error(detail || "请求失败（HTTP " + response.status + "）");
    }
    return data;
  }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function make(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function safeTitle(text) {
    return String(text == null ? "" : text).replace(/[<>]/g, "");
  }

  function scrollBottom() {
    var log = $("chatLog");
    log.scrollTop = log.scrollHeight;
  }

  function setChip(id, level, text, title) {
    var chip = $(id);
    if (!chip) return;
    chip.classList.remove("is-ok", "is-bad", "is-warn");
    if (level) chip.classList.add(level);
    chip.querySelector("span").textContent = text;
    chip.title = title || text;
  }

  function fmtTime(text) {
    return text ? String(text).replace(/^\d{4}-/, "").slice(0, 16) : "";
  }

  // --------------------- 安全渲染（DOM 白名单构建） ---------------------
  var BLOCKED_TAGS = [
    "script", "style", "iframe", "object", "embed", "form", "input",
    "button", "link", "meta", "base", "svg", "math"
  ];
  var URL_ATTRS = ["href", "src", "xlink:href"];

  function parseFragment(html) {
    var range = document.createRange();
    range.selectNodeContents(document.body);
    // Range 解析出的内容不会执行脚本，仅用于后续白名单清洗
    return range.createContextualFragment(String(html == null ? "" : html));
  }

  function sanitizeNodes(html) {
    var fragment = parseFragment(html);

    BLOCKED_TAGS.forEach(function (tag) {
      var nodes = fragment.querySelectorAll(tag);
      for (var index = nodes.length - 1; index >= 0; index -= 1) {
        nodes[index].remove();
      }
    });

    Array.prototype.forEach.call(fragment.querySelectorAll("*"), function (node) {
      Array.prototype.slice.call(node.attributes || []).forEach(function (attr) {
        var name = attr.name.toLowerCase();
        if (name.startsWith("on")) {
          node.removeAttribute(attr.name);
          return;
        }
        if (URL_ATTRS.includes(name) && /^\s*(javascript|vbscript|data:text\/html)/i.test(attr.value)) {
          node.removeAttribute(attr.name);
        }
      });
      if (node.tagName === "A") {
        node.setAttribute("target", "_blank");
        node.setAttribute("rel", "noopener noreferrer");
      }
    });

    return Array.prototype.slice.call(fragment.childNodes);
  }

  function setHtml(element, html) {
    var nodes = sanitizeNodes(html);
    element.replaceChildren.apply(element, nodes);
  }

  function appendHtml(element, html) {
    sanitizeNodes(html).forEach(function (node) {
      element.append(node);
    });
  }

  // ------------------------------ 配置 / 状态 ---------------------------
  async function loadConfig() {
    try {
      var data = await api("/api/config");
      state.config = data;
      if (data.limits && data.limits.max_upload_mb) {
        state.maxUpload = data.limits.max_upload_mb;
        $("maxUpload").textContent = state.maxUpload;
      }
      renderModelChip(data);
    } catch (err) {
      /* 配置拿不到不阻塞主流程 */
    }
  }

  function renderModelChip(config) {
    if (config.mock_mode) {
      setChip("chipModel", "is-warn", "离线演示模式", "LLM_MOCK=1：不调用真实模型");
      return;
    }
    if (config.llm_ready) {
      setChip("chipModel", "is-ok", config.model || "模型已就绪", (config.provider_label || "") + " · " + config.model);
      return;
    }
    setChip("chipModel", "is-bad", "模型未配置", "请在 .env 填写 LLM_API_KEY（或 QWEN_API_KEY）");
  }

  async function loadHealth() {
    try {
      var data = await api("/api/health");
      state.health = data;

      var kafka = data.kafka || {};
      setChip(
        "chipKafka",
        kafka.ok ? "is-ok" : "is-bad",
        kafka.ok ? "Kafka 已连接" : "Kafka 未连接",
        (kafka.bootstrap || "") + " · " + (kafka.message || "")
      );

      var parser = data.parser || {};
      var parserText = "内置解析器";
      var parserLevel = "is-warn";
      if (parser.active === "mineru") {
        parserText = "MinerU 解析";
        parserLevel = "is-ok";
      } else if (parser.active !== "lite") {
        parserText = "解析引擎异常";
      }
      setChip("chipParser", parserLevel, parserText, parser.detail || "");

      var worker = data.worker || {};
      setChip(
        "chipWorker",
        worker.online ? "is-ok" : "is-bad",
        worker.online ? "工作进程在线" : "工作进程离线",
        worker.online ? "模式：" + worker.mode : worker.error || "请确认 worker 已启动"
      );

      if (data.llm) {
        renderModelChip({
          llm_ready: data.llm.ready,
          mock_mode: data.llm.mock,
          model: data.llm.model,
          provider_label: data.llm.provider_label
        });
      }
    } catch (err) {
      setChip("chipKafka", "is-bad", "服务不可达", err.message);
    }
  }

  // ------------------------------ 文档列表 ------------------------------
  async function loadDocs(quiet) {
    try {
      var data = await api("/api/documents");
      state.docs = data.items || [];
      renderDocs(data.counts || {});
      renderScopeOptions();
    } catch (err) {
      if (!quiet) layer.msg(err.message, { icon: 2 });
    }
  }

  function statusPill(doc) {
    var pct = Math.round((doc.progress || 0) * 100);
    if (doc.status === "pending") {
      return '<span class="pill pill-pending"><i class="dot"></i>待解析</span>';
    }
    if (doc.status === "parsing") {
      return '<span class="pill pill-parsing"><i class="dot"></i>' + esc(doc.stage || "解析中") + " " + pct + "%</span>";
    }
    if (doc.status === "done") {
      return '<span class="pill pill-done"><i class="dot"></i>解析完成</span>';
    }
    return '<span class="pill pill-failed"><i class="dot"></i>解析失败</span>';
  }

  function renderDocs(counts) {
    var summary;
    if (counts && counts.total !== undefined) {
      summary =
        counts.total + " 篇文档 · 待解析 " + counts.pending +
        " · 解析中 " + counts.parsing + " · 完成 " + counts.done +
        (counts.failed ? " · 失败 " + counts.failed : "");
    } else {
      summary = state.docs.length + " 篇文档";
    }
    $("docSummary").textContent = summary;

    var list = $("docList");
    if (!state.docs.length) {
      setHtml(list, '<div class="empty">还没有文档，先上传一个 PDF 吧</div>');
      return;
    }

    var html = state.docs.map(function (doc) {
      var parsing = doc.status === "parsing";
      var pct = Math.round((doc.progress || 0) * 100);
      var meta = [
        doc.size_human,
        doc.page_count ? doc.page_count + " 页" : "",
        doc.chunk_count ? doc.chunk_count + " 块" : "",
        doc.figure_count ? doc.figure_count + " 图" : "",
        doc.parser || "",
        doc.source || "",
        fmtTime(doc.updated_at)
      ].filter(Boolean).join(" · ");

      return (
        '<article class="doc-card" data-id="' + esc(doc.id) + '">' +
          '<div class="doc-main">' +
            '<div class="doc-name" title="' + esc(doc.filename) + '">' +
              '<i class="layui-icon layui-icon-file-pdf"></i>' + esc(doc.filename) +
            "</div>" +
            '<div class="doc-meta" title="' + esc(meta) + '">' + esc(meta) + "</div>" +
          "</div>" +
          '<div class="doc-status">' + statusPill(doc) + "</div>" +
          (parsing ? '<div class="doc-bar"><i style="width:' + pct + '%"></i></div>' : "") +
          (doc.status === "failed" && doc.error ? '<div class="doc-error">' + esc(doc.error) + "</div>" : "") +
          '<div class="doc-actions">' +
            '<button type="button" class="mini" data-act="preview" ' + (doc.status === "done" ? "" : "disabled") + ">预览</button>" +
            '<button type="button" class="mini" data-act="reparse" ' + (parsing ? "disabled" : "") + ">重新解析</button>" +
            '<button type="button" class="mini mini-danger" data-act="delete">删除</button>' +
          "</div>" +
        "</article>"
      );
    }).join("");
    setHtml(list, html);
  }

  function findDoc(id) {
    return state.docs.find(function (doc) {
      return doc.id === id;
    });
  }

  async function previewDoc(doc) {
    var loading = layer.load(1, { shade: 0.2 });
    try {
      var data = await api("/api/documents/" + encodeURIComponent(doc.id) + "/preview");
      var figures = { items: [] };
      try {
        figures = await api("/api/documents/" + encodeURIComponent(doc.id) + "/figures");
      } catch (err) {
        figures = { items: [] };
      }

      var content = make("div", "preview-modal");
      content.append(make(
        "div",
        "preview-meta",
        "解析器：" + (doc.parser || "-") + " · " + doc.page_count + " 页 · " +
          doc.chunk_count + " 块 · " + doc.figure_count + " 张图"
      ));

      var body = make("div", "answer-html");
      appendHtml(body, data.html || "<p>（没有内容）</p>");
      content.append(body);

      var figureItems = (figures.items || []).slice(0, 12);
      if (figureItems.length) {
        var strip = make("div", "preview-figures");
        figureItems.forEach(function (item) {
          var figure = document.createElement("figure");
          var image = document.createElement("img");
          image.src = item.image_url;
          image.alt = item.caption || "插图";
          image.loading = "lazy";
          figure.append(image);
          figure.append(make("figcaption", null, "第 " + item.page + " 页 · " + (item.caption || "插图")));
          strip.append(figure);
        });
        content.append(strip);
      }

      layer.open({
        type: 1,
        title: "解析结果 · " + safeTitle(doc.filename),
        area: [Math.min(940, Math.round(window.innerWidth * 0.92)) + "px", "82vh"],
        shadeClose: true,
        content: content
      });
    } catch (err) {
      layer.msg(err.message, { icon: 2 });
    } finally {
      layer.close(loading);
    }
  }

  function reparseDoc(doc) {
    layer.confirm(
      "重新解析《" + esc(doc.filename) + "》？会重新走一遍 Kafka 解析流程。",
      { title: "重新解析", btn: ["开始", "取消"] },
      async function (index) {
        layer.close(index);
        try {
          await api("/api/documents/" + encodeURIComponent(doc.id) + "/reparse", { method: "POST" });
          layer.msg("已重新投递解析任务", { icon: 1 });
          loadDocs(true);
        } catch (err) {
          layer.msg(err.message, { icon: 2 });
        }
      }
    );
  }

  function deleteDoc(doc) {
    layer.confirm(
      "删除《" + esc(doc.filename) + "》？文档、解析结果和索引都会被移除。",
      { title: "删除文档", btn: ["删除", "取消"] },
      async function (index) {
        layer.close(index);
        try {
          await api("/api/documents/" + encodeURIComponent(doc.id), { method: "DELETE" });
          layer.msg("已删除", { icon: 1 });
          loadDocs(true);
        } catch (err) {
          layer.msg(err.message, { icon: 2 });
        }
      }
    );
  }

  // ------------------------------ 上传 ----------------------------------
  function setupUpload() {
    upload.render({
      elem: "#uploadZone",
      url: "/api/documents",
      field: "files",
      multiple: true,
      number: 0,
      drag: true,
      accept: "file",
      exts: "pdf",
      size: state.maxUpload * 1024,
      choose: function () {
        state.busyUpload += 1;
        showUploadProgress(2, "上传中…");
      },
      progress: function (percent) {
        showUploadProgress(percent, "上传中 " + percent + "%");
      },
      done: function (res) {
        if (res && res.detail) layer.msg(res.detail, { icon: 2 });
        var warnings = (res && res.warnings) || [];
        warnings.forEach(function (text) {
          layer.msg(text, { icon: 0, time: 3000 });
        });
        if (res && res.items && res.items.length) {
          layer.msg("已投递 " + res.items.length + " 个解析任务", { icon: 1, time: 1600 });
        }
        loadDocs(true);
      },
      allDone: function () {
        state.busyUpload = Math.max(0, state.busyUpload - 1);
        finishUploadProgress();
        loadDocs(true);
      },
      error: function () {
        state.busyUpload = Math.max(0, state.busyUpload - 1);
        finishUploadProgress();
        layer.msg("上传失败，请检查网络、文件大小或后端日志", { icon: 2 });
      }
    });
  }

  function showUploadProgress(percent, text) {
    $("uploadProgress").hidden = false;
    $("uploadBar").style.width = Math.max(2, Math.min(100, percent)) + "%";
    $("uploadText").textContent = text || "上传中…";
  }

  function finishUploadProgress() {
    showUploadProgress(100, "已完成");
    setTimeout(function () {
      $("uploadProgress").hidden = true;
      $("uploadBar").style.width = "0%";
    }, 900);
  }

  // ------------------------------ 检索范围 ------------------------------
  function renderScopeOptions() {
    var done = state.docs.filter(function (doc) {
      return doc.status === "done";
    });
    var doneIds = new Set(done.map(function (doc) { return doc.id; }));
    state.selected.forEach(function (id) {
      if (!doneIds.has(id)) state.selected.delete(id);
    });

    var options = $("scopeOptions");
    if (!done.length) {
      setHtml(options, '<div class="empty">还没有解析完成的文档</div>');
    } else {
      var rows = [
        '<label class="scope-item"><input type="checkbox" data-all="1" ' +
          (state.selected.size === 0 ? "checked" : "") +
          " /> <span>全部文档（" + done.length + " 篇）</span></label>"
      ];
      done.forEach(function (doc) {
        rows.push(
          '<label class="scope-item"><input type="checkbox" data-doc="' + esc(doc.id) + '" ' +
          (state.selected.has(doc.id) ? "checked" : "") +
          " /> <span>" + esc(doc.filename) + "</span></label>"
        );
      });
      setHtml(options, rows.join(""));
    }
    $("scopeLabel").textContent = state.selected.size
      ? "检索范围：已选 " + state.selected.size + " 篇"
      : "检索范围：全部文档";
  }

  function bindScopeEvents() {
    $("scopeToggle").addEventListener("click", function (event) {
      event.stopPropagation();
      var menu = $("scopeMenu");
      menu.hidden = !menu.hidden;
    });

    $("scopeOptions").addEventListener("change", function (event) {
      var input = event.target;
      if (!input || input.tagName !== "INPUT") return;
      if (input.dataset.all) {
        state.selected.clear();
      } else if (input.dataset.doc) {
        if (input.checked) state.selected.add(input.dataset.doc);
        else state.selected.delete(input.dataset.doc);
      }
      renderScopeOptions();
    });

    document.addEventListener("click", function (event) {
      if (!$("scope").contains(event.target)) $("scopeMenu").hidden = true;
    });
  }

  // ------------------------------ 图片处理 ------------------------------
  function fileToDataUrl(file, maxSide, quality) {
    return new Promise(function (resolve, reject) {
      var url = URL.createObjectURL(file);
      var image = new Image();
      image.onload = function () {
        var scale = Math.min(1, (maxSide || 1000) / Math.max(image.width, image.height));
        var canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(image.width * scale));
        canvas.height = Math.max(1, Math.round(image.height * scale));
        var context = canvas.getContext("2d");
        context.fillStyle = "#fff";
        context.fillRect(0, 0, canvas.width, canvas.height);
        context.drawImage(image, 0, 0, canvas.width, canvas.height);
        URL.revokeObjectURL(url);
        resolve(canvas.toDataURL("image/jpeg", quality || 0.86));
      };
      image.onerror = function () {
        URL.revokeObjectURL(url);
        reject(new Error("图片读取失败"));
      };
      image.src = url;
    });
  }

  async function handleImageFile(file) {
    if (!file) return;
    if (!/^image\//.test(file.type || "")) {
      layer.msg("请选择图片文件", { icon: 0 });
      return;
    }
    try {
      state.image = await fileToDataUrl(file);
      $("imageThumb").src = state.image;
      $("imagePreview").hidden = false;
    } catch (err) {
      layer.msg(err.message, { icon: 2 });
    }
  }

  function clearImage() {
    state.image = null;
    $("imagePreview").hidden = true;
    $("imageThumb").removeAttribute("src");
    $("imageInput").value = "";
  }

  // ------------------------------ 对话渲染 ------------------------------
  function hideWelcome() {
    var welcome = $("welcome");
    if (welcome) welcome.remove();
  }

  function kindBadge(source) {
    var label = "正文";
    var className = "";
    if (source.kind === "figure") {
      label = "图片";
      className = "badge-figure";
    } else if (source.kind === "table") {
      label = "表格";
      className = "badge-table";
    }
    return '<span class="badge ' + className + '">' + label + " " + source.rank + "</span>";
  }

  function sourcesHtml(sources) {
    if (!sources || !sources.length) return "";
    var items = sources.map(function (source) {
      var thumb = source.image_url || source.page_image_url;
      return (
        '<div class="source-item">' +
          (thumb ? '<img class="source-thumb" src="' + esc(thumb) + '" alt="" loading="lazy" />' : "") +
          '<div class="source-body">' +
            '<div class="source-title">' + kindBadge(source) + esc(source.filename) + " · 第 " + source.page + " 页" +
              (source.section ? " · " + esc(source.section) : "") +
            "</div>" +
            '<div class="source-snippet">' + esc(source.snippet || "") + "</div>" +
          "</div>" +
        "</div>"
      );
    }).join("");
    return '<details class="sources"><summary>引用资料 ' + sources.length + " 条</summary>" + items + "</details>";
  }

  function appendUser(text, imageUrl) {
    hideWelcome();
    var node = make("div", "msg msg-user");
    setHtml(
      node,
      '<div class="avatar">我</div><div class="bubble">' +
        (imageUrl ? '<div class="msg-images"><img src="' + esc(imageUrl) + '" alt="提问图片" /></div>' : "") +
        '<div class="msg-text">' + esc(text || "（图片提问）") + "</div>" +
      "</div>"
    );
    $("chatLog").append(node);
    scrollBottom();
    return node;
  }

  function appendAssistant(message, extra) {
    hideWelcome();
    var node = make("div", "msg msg-assistant");

    var metaParts = [];
    if (message.model) metaParts.push(esc(message.model));
    if (message.elapsed_ms) metaParts.push((message.elapsed_ms / 1000).toFixed(1) + " s");
    if (extra && extra.retrieval) {
      metaParts.push("召回 正文 " + extra.retrieval.text_hits + " · 图片 " + extra.retrieval.figure_hits);
      if (extra.retrieval.elapsed_ms) metaParts.push("检索 " + extra.retrieval.elapsed_ms + " ms");
    }
    if (extra && extra.mock) metaParts.push("演示模式");

    var warnings = (extra && extra.warnings) || [];
    var warningHtml = warnings.map(function (text) {
      return '<div class="note">' + esc(text) + "</div>";
    }).join("");

    setHtml(
      node,
      '<div class="avatar">AI</div><div class="bubble">' +
        '<div class="answer-html">' + (message.content_html || esc(message.content_md || "")) + "</div>" +
        warningHtml +
        sourcesHtml(message.sources) +
        '<div class="msg-meta"><span>' + metaParts.join(" · ") + "</span></div>" +
      "</div>"
    );
    $("chatLog").append(node);
    scrollBottom();
    return node;
  }

  function appendError(text) {
    hideWelcome();
    var node = make("div", "msg msg-assistant");
    setHtml(
      node,
      '<div class="avatar">AI</div><div class="bubble"><div class="note note-error">' + esc(text) + "</div></div>"
    );
    $("chatLog").append(node);
    scrollBottom();
  }

  function appendTyping() {
    var node = make("div", "msg msg-assistant");
    node.dataset.typing = "1";
    setHtml(
      node,
      '<div class="avatar">AI</div><div class="bubble"><span class="typing"><i></i><i></i><i></i></span>' +
      '<div class="msg-meta"><span>正在检索资料并生成回答…</span></div></div>'
    );
    $("chatLog").append(node);
    scrollBottom();
    return node;
  }

  function setSending(sending) {
    state.sending = sending;
    var button = $("btnSend");
    button.disabled = sending;
    button.querySelector("span").textContent = sending ? "生成中…" : "发送";
  }

  function welcomeNode(text) {
    var wrap = make("div", "welcome");
    wrap.id = "welcome";
    wrap.append(make("div", "welcome-title", "把 PDF 交给它，然后开始提问"));
    if (text) wrap.append(make("p", "welcome-tip", text));
    return wrap;
  }

  // ------------------------------ 发送 ----------------------------------
  async function sendQuestion() {
    if (state.sending) return;
    var input = $("question");
    var question = input.value.trim();
    if (!question && !state.image) {
      layer.msg("请输入问题，或添加一张图片", { icon: 0 });
      return;
    }
    if (!state.docs.some(function (doc) { return doc.status === "done"; })) {
      layer.msg("还没有解析完成的文档，请先上传 PDF 并等待解析完成", { icon: 0 });
      return;
    }

    var image = state.image;
    appendUser(question, image);
    input.value = "";
    autoGrow(input);
    clearImage();
    setSending(true);
    var typing = appendTyping();

    try {
      var payload = {
        question: question,
        session_id: state.sessionId,
        doc_ids: Array.from(state.selected),
        image: image
      };
      var result = await api("/api/chat", { method: "POST", body: payload });
      typing.remove();
      if (result.session_id) {
        state.sessionId = result.session_id;
        localStorage.setItem("rag.session", result.session_id);
      }
      appendAssistant(result.message, result);
    } catch (err) {
      typing.remove();
      appendError(err.message);
    } finally {
      setSending(false);
      input.focus();
    }
  }

  async function loadHistory() {
    if (!state.sessionId) return;
    try {
      var data = await api("/api/chat/" + encodeURIComponent(state.sessionId));
      var messages = (data && data.messages) || [];
      if (!messages.length) return;
      hideWelcome();
      messages.forEach(function (message) {
        if (message.role === "user") {
          var image = (message.images || [])[0];
          appendUser(message.content_md, image ? image.url : null);
        } else {
          appendAssistant(message, null);
        }
      });
    } catch (err) {
      localStorage.removeItem("rag.session");
      state.sessionId = null;
    }
  }

  async function clearChat() {
    if (!state.sessionId) {
      layer.msg("当前没有对话", { icon: 0 });
      return;
    }
    layer.confirm("清空当前会话的全部消息？", { title: "清空对话" }, async function (index) {
      layer.close(index);
      try {
        await api("/api/chat/" + encodeURIComponent(state.sessionId), { method: "DELETE" });
      } catch (err) {
        /* 服务端清理失败也继续清空界面 */
      }
      localStorage.removeItem("rag.session");
      state.sessionId = null;
      $("chatLog").replaceChildren(welcomeNode("对话已清空，可以开始新的提问。"));
      layer.msg("已清空", { icon: 1 });
    });
  }

  // ------------------------------ 事件绑定 ------------------------------
  function autoGrow(textarea) {
    textarea.style.height = "auto";
    textarea.style.height = Math.min(160, textarea.scrollHeight) + "px";
  }

  function bindEvents() {
    $("btnRefresh").addEventListener("click", function () {
      loadDocs(false);
      loadHealth();
    });

    $("docList").addEventListener("click", function (event) {
      var button = event.target.closest("[data-act]");
      if (!button) return;
      var card = button.closest(".doc-card");
      var doc = card ? findDoc(card.dataset.id) : null;
      if (!doc) return;
      var action = button.dataset.act;
      if (action === "preview") previewDoc(doc);
      if (action === "reparse") reparseDoc(doc);
      if (action === "delete") deleteDoc(doc);
    });

    $("btnSend").addEventListener("click", sendQuestion);
    $("btnClear").addEventListener("click", clearChat);

    var question = $("question");
    question.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        sendQuestion();
      }
    });
    question.addEventListener("input", function () { autoGrow(question); });
    question.addEventListener("paste", function (event) {
      var items = (event.clipboardData && event.clipboardData.items) || [];
      for (var index = 0; index < items.length; index += 1) {
        if (items[index].type && items[index].type.startsWith("image")) {
          event.preventDefault();
          handleImageFile(items[index].getAsFile());
          break;
        }
      }
    });

    $("btnImage").addEventListener("click", function () { $("imageInput").click(); });
    $("imageInput").addEventListener("change", function (event) {
      handleImageFile(event.target.files && event.target.files[0]);
    });
    $("btnRemoveImage").addEventListener("click", clearImage);

    $("chatLog").addEventListener("click", function (event) {
      var image = event.target.closest("img");
      if (!image) return;
      var box = make("div", "lightbox");
      var preview = document.createElement("img");
      preview.src = image.src;
      preview.alt = image.alt || "";
      box.append(preview);
      layer.open({
        type: 1,
        title: safeTitle(image.alt || "图片预览"),
        area: ["auto", "auto"],
        shadeClose: true,
        content: box
      });
    });

    $("welcome").addEventListener("click", function (event) {
      var example = event.target.closest(".example");
      if (!example) return;
      $("question").value = example.dataset.q || example.textContent;
      autoGrow($("question"));
      sendQuestion();
    });

    bindScopeEvents();
  }

  // ------------------------------ 轮询 ----------------------------------
  function startPolling() {
    setInterval(loadHealth, 12000);
    setInterval(function () {
      if (state.busyUpload > 0) return;
      var busy = state.docs.some(function (doc) {
        return doc.status === "pending" || doc.status === "parsing";
      });
      if (busy) loadDocs(true);
    }, 2200);
    setInterval(function () {
      var busy = state.docs.some(function (doc) {
        return doc.status === "pending" || doc.status === "parsing";
      });
      if (!busy) loadDocs(true);
    }, 30000);
  }
})();
