/* Chinook 数据查询台：原生 JS + layui layer。 */
(function () {
  "use strict";

  var STORAGE_PREFIX = "chinook-agent.";
  var QUERY_TIMEOUT_MS = 300000;
  var MAX_EVIDENCE = 12;

  var state = {
    config: null,
    configError: false,
    baseUrl: "",
    apiKey: "",
    model: "",
    summaryModel: "",
    records: new Map(),
    nextId: 1,
    querying: false,
    stats: { total: 0, success: 0, elapsed: [] },
  };

  var layer = null;
  var drawerTimer = null;

  var $ = function (id) {
    return document.getElementById(id);
  };

  var els = {};

  function cacheElements() {
    els.form = $("query-form");
    els.input = $("question");
    els.submit = $("btn-query");
    els.feed = $("feed");
    els.emptyState = $("empty-state");
    els.emptyActions = $("empty-actions");
    els.exampleList = $("example-list");
    els.serverDot = $("server-dot");
    els.serverText = $("server-text");
    els.dbFile = $("db-file");
    els.tableCount = $("table-count");
    els.tableList = $("table-list");
    els.modelBadge = $("model-badge");
    els.statTotal = $("stat-total");
    els.statSuccess = $("stat-success");
    els.statElapsed = $("stat-elapsed");
    els.btnSettings = $("btn-settings");
    els.drawer = $("settings-drawer");
    els.drawerMask = $("drawer-mask");
    els.btnCloseDrawer = $("btn-close-drawer");
    els.settingsForm = $("settings-form");
    els.inputBaseUrl = $("input-base-url");
    els.inputApiKey = $("input-api-key");
    els.inputModel = $("input-model");
    els.inputSummaryModel = $("input-summary-model");
    els.keyStatus = $("key-status");
    els.baseUrlHint = $("base-url-hint");
    els.btnResetSettings = $("btn-reset-settings");
    els.linkSchema = $("link-schema");
  }

  /* ---------- 工具函数 ---------- */

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function escapeAttr(value) {
    return escapeHtml(value).replace(/\n/g, " ");
  }

  function formatInt(value) {
    var number = Number(value);
    if (!isFinite(number)) {
      return String(value == null ? "" : value);
    }
    return number.toLocaleString("zh-CN");
  }

  function pad2(value) {
    return value < 10 ? "0" + value : String(value);
  }

  function timeLabel(date) {
    var d = date || new Date();
    return pad2(d.getHours()) + ":" + pad2(d.getMinutes()) + ":" + pad2(d.getSeconds());
  }

  function stamp(date) {
    var d = date || new Date();
    return (
      d.getFullYear() +
      pad2(d.getMonth() + 1) +
      pad2(d.getDate()) +
      "-" +
      pad2(d.getHours()) +
      pad2(d.getMinutes()) +
      pad2(d.getSeconds())
    );
  }

  function toast(message, icon) {
    if (layer) {
      layer.msg(message, { icon: icon == null ? 0 : icon, time: 1800 });
    } else {
      window.console.log(message);
    }
  }

  function prefersReducedMotion() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function scrollToElement(element) {
    if (!element) {
      return;
    }
    element.scrollIntoView({
      behavior: prefersReducedMotion() ? "auto" : "smooth",
      block: "start",
    });
  }

  function looksLikeUrl(value) {
    try {
      var parsed = new URL(value);
      return (parsed.protocol === "http:" || parsed.protocol === "https:") && !!parsed.hostname;
    } catch (error) {
      return false;
    }
  }

  function maskKey(key) {
    if (!key) {
      return "";
    }
    if (key.length <= 8) {
      return "••••••••";
    }
    return key.slice(0, 3) + "••••••••" + key.slice(-4);
  }

  /* ---------- 本地存储 ---------- */

  var localStore = {
    get: function (key) {
      try {
        return window.localStorage.getItem(STORAGE_PREFIX + key);
      } catch (error) {
        return null;
      }
    },
    set: function (key, value) {
      try {
        if (value) {
          window.localStorage.setItem(STORAGE_PREFIX + key, value);
        } else {
          window.localStorage.removeItem(STORAGE_PREFIX + key);
        }
      } catch (error) {
        /* 隐私模式下忽略 */
      }
    },
  };

  var sessionStore = {
    get: function (key) {
      try {
        return window.sessionStorage.getItem(STORAGE_PREFIX + key);
      } catch (error) {
        return null;
      }
    },
    set: function (key, value) {
      try {
        if (value) {
          window.sessionStorage.setItem(STORAGE_PREFIX + key, value);
        } else {
          window.sessionStorage.removeItem(STORAGE_PREFIX + key);
        }
      } catch (error) {
        /* 隐私模式下忽略 */
      }
    },
  };

  function loadStoredSettings() {
    state.baseUrl = localStore.get("baseUrl") || "";
    state.model = localStore.get("model") || "";
    state.summaryModel = localStore.get("summaryModel") || "";
    state.apiKey = sessionStore.get("apiKey") || "";
  }

  function persistSettings() {
    localStore.set("baseUrl", state.baseUrl);
    localStore.set("model", state.model);
    localStore.set("summaryModel", state.summaryModel);
    sessionStore.set("apiKey", state.apiKey);
  }

  /* ---------- 服务状态与初始化配置 ---------- */

  function setServerStatus(ok, text) {
    els.serverDot.className = "dot " + (ok ? "is-ok" : "is-error");
    els.serverText.textContent = text;
  }

  async function loadConfig() {
    try {
      var response = await fetch("/api/config", {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error("HTTP " + response.status);
      }
      state.config = await response.json();
      state.configError = false;
      renderConfig();
      setServerStatus(true, "服务已连接");
    } catch (error) {
      state.configError = true;
      setServerStatus(false, "服务不可用");
      els.tableList.innerHTML = '<p class="rail-placeholder">无法读取数据库信息，请确认服务是否正常。</p>';
      renderKeyStatus();
    }
  }

  function renderConfig() {
    var config = state.config;
    if (!config) {
      return;
    }

    els.dbFile.textContent = (config.database && config.database.file) || "chinook.db";

    var tables = (config.database && config.database.tables) || [];
    els.tableCount.textContent = tables.length + " 张表";
    if (tables.length) {
      els.tableList.innerHTML = tables
        .map(function (table) {
          return (
            '<div class="table-row">' +
            '<span class="table-name" title="' + escapeAttr(table.name) + '">' +
            escapeHtml(table.name) +
            "</span>" +
            '<span class="table-meta">' + formatInt(table.rows) + " 行 · " + formatInt(table.columns) + " 列</span>" +
            "</div>"
          );
        })
        .join("");
    } else {
      els.tableList.innerHTML = '<p class="rail-placeholder">数据库中暂无业务表。</p>';
    }

    els.inputBaseUrl.placeholder = config.base_url || "https://api.openai.com/v1";
    els.inputModel.placeholder = config.primary_model || "服务端默认";
    els.inputSummaryModel.placeholder = config.summary_model || "服务端默认";
    els.baseUrlHint.textContent = config.base_url
      ? "留空使用服务端默认端点：" + config.base_url
      : "留空使用服务端默认端点。";

    renderExamples(config.example_questions || []);
    renderModelBadge();
    renderKeyStatus();
    renderSettingsInputs();
  }

  function renderExamples(questions) {
    els.exampleList.innerHTML = "";
    els.emptyActions.innerHTML = "";

    if (!questions.length) {
      els.exampleList.innerHTML = '<span class="side-placeholder">暂无示例</span>';
      return;
    }

    questions.slice(0, 6).forEach(function (question) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "example-item";
      button.textContent = question;
      button.addEventListener("click", function () {
        useQuestion(question);
      });
      els.exampleList.appendChild(button);
    });

    questions.slice(0, 3).forEach(function (question) {
      var chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip";
      chip.textContent = question;
      chip.addEventListener("click", function () {
        useQuestion(question);
      });
      els.emptyActions.appendChild(chip);
    });
  }

  function renderModelBadge() {
    var primary =
      state.model || (state.config && state.config.primary_model) || "未配置模型";
    var summary =
      state.summaryModel || (state.config && state.config.summary_model) || primary;
    els.modelBadge.textContent = primary;
    els.modelBadge.title = "主模型：" + primary + "；汇总模型：" + summary;
  }

  function renderKeyStatus() {
    var hasServerKey = !!(state.config && state.config.has_server_api_key);
    if (state.apiKey) {
      els.keyStatus.className = "drawer-tip";
      els.keyStatus.innerHTML =
        "<strong>已填写密钥</strong>：" + escapeHtml(maskKey(state.apiKey)) + "，仅保存在当前标签页。";
    } else if (!state.config) {
      els.keyStatus.className = "drawer-tip" + (state.configError ? " is-warn" : "");
      els.keyStatus.textContent = state.configError
        ? "无法读取服务端配置，请确认服务已启动。"
        : "正在读取服务端配置…";
    } else if (hasServerKey) {
      els.keyStatus.className = "drawer-tip";
      els.keyStatus.innerHTML = "<strong>使用服务端密钥</strong>：服务端已配置 OPENAI_API_KEY。";
    } else {
      els.keyStatus.className = "drawer-tip is-warn";
      els.keyStatus.innerHTML =
        "<strong>未配置密钥</strong>：查询前请填写 API Key，或为服务端设置 OPENAI_API_KEY 环境变量。";
    }
  }

  function renderSettingsInputs() {
    els.inputBaseUrl.value = state.baseUrl;
    els.inputApiKey.value = state.apiKey;
    els.inputModel.value = state.model;
    els.inputSummaryModel.value = state.summaryModel;
  }

  /* ---------- 设置抽屉 ---------- */

  function openDrawer() {
    if (drawerTimer) {
      window.clearTimeout(drawerTimer);
      drawerTimer = null;
    }
    els.drawerMask.hidden = false;
    els.drawer.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    window.requestAnimationFrame(function () {
      els.drawerMask.classList.add("is-open");
      els.drawer.classList.add("is-open");
    });
    window.setTimeout(function () {
      els.inputBaseUrl.focus();
    }, 240);
  }

  function closeDrawer() {
    els.drawer.classList.remove("is-open");
    els.drawerMask.classList.remove("is-open");
    els.drawer.setAttribute("aria-hidden", "true");
    document.body.style.overflow = "";
    drawerTimer = window.setTimeout(function () {
      els.drawerMask.hidden = true;
      drawerTimer = null;
    }, 220);
  }

  function saveSettings(event) {
    event.preventDefault();
    var baseUrl = els.inputBaseUrl.value.trim().replace(/\/+$/, "");
    var apiKey = els.inputApiKey.value.trim();
    var model = els.inputModel.value.trim();
    var summaryModel = els.inputSummaryModel.value.trim();

    if (baseUrl && !looksLikeUrl(baseUrl)) {
      toast("接口地址需以 http:// 或 https:// 开头", 2);
      els.inputBaseUrl.focus();
      return;
    }

    state.baseUrl = baseUrl;
    state.apiKey = apiKey;
    state.model = model;
    state.summaryModel = summaryModel;
    persistSettings();
    renderModelBadge();
    renderKeyStatus();
    renderSettingsInputs();
    closeDrawer();
    toast("设置已保存", 1);
  }

  function resetSettings() {
    state.baseUrl = "";
    state.apiKey = "";
    state.model = "";
    state.summaryModel = "";
    persistSettings();
    renderSettingsInputs();
    renderModelBadge();
    renderKeyStatus();
    toast("已恢复服务端默认配置", 1);
  }

  /* ---------- 查询 ---------- */

  function useQuestion(question) {
    els.input.value = question;
    autosizeInput();
    els.input.focus();
    var card = document.querySelector(".query-card");
    if (card && card.getBoundingClientRect().top < 0) {
      scrollToElement(card);
    }
  }

  function autosizeInput() {
    els.input.style.height = "auto";
    els.input.style.height = Math.max(92, Math.min(els.input.scrollHeight, 180)) + "px";
  }

  function setQuerying(active) {
    state.querying = active;
    els.submit.disabled = active;
    els.submit.classList.toggle("layui-btn-disabled", active);
    var label = els.submit.querySelector("span");
    if (label) {
      label.textContent = active ? "查询中…" : "开始查询";
    }
  }

  function buildPayload(question) {
    var payload = { question: question };
    var llm = {};
    if (state.baseUrl) {
      llm.base_url = state.baseUrl;
    }
    if (state.apiKey) {
      llm.api_key = state.apiKey;
    }
    if (state.model) {
      llm.model = state.model;
    }
    if (state.summaryModel) {
      llm.summary_model = state.summaryModel;
    }
    if (Object.keys(llm).length) {
      payload.llm = llm;
    }
    return payload;
  }

  async function submitQuery(event) {
    if (event) {
      event.preventDefault();
    }
    if (state.querying) {
      return;
    }

    var question = els.input.value.trim();
    if (!question) {
      toast("请输入查询问题", 0);
      els.input.focus();
      return;
    }

    var hasServerKey = !!(state.config && state.config.has_server_api_key);
    if (!state.apiKey && !hasServerKey) {
      toast("请先在“接口设置”中填写 API Key", 0);
      openDrawer();
      return;
    }

    hideEmptyState();
    setQuerying(true);

    var id = state.nextId++;
    var startedAt = new Date();
    var timerStart = window.performance ? window.performance.now() : Date.now();
    els.input.value = "";
    autosizeInput();

    var pending = appendPending(id, question, startedAt);
    var controller = new AbortController();
    var timeout = window.setTimeout(function () {
      controller.abort();
    }, QUERY_TIMEOUT_MS);

    try {
      var response = await fetch("/api/query", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(buildPayload(question)),
        signal: controller.signal,
      });

      var data = await readJson(response);
      var elapsed = elapsedSeconds(timerStart);
      pending.remove();

      if (!response.ok) {
        var message = (data && (data.detail || data.message)) || "请求失败（HTTP " + response.status + "）";
        if (Array.isArray(message)) {
          message = message
            .map(function (item) {
              return item.msg || JSON.stringify(item);
            })
            .join("；");
        }
        appendFailure(id, question, startedAt, message, elapsed);
        updateStats(false, elapsed);
        return;
      }

      appendResult(id, question, startedAt, data, elapsed);
      updateStats(!data.error, elapsed);
    } catch (error) {
      var elapsedOnError = elapsedSeconds(timerStart);
      pending.remove();
      var text =
        error && error.name === "AbortError"
          ? "请求超时（300 秒）。请稍后重试，或简化问题、减少联表数量。"
          : (error && error.message) || "网络异常，无法连接服务。";
      appendFailure(id, question, startedAt, text, elapsedOnError);
      updateStats(false, elapsedOnError);
    } finally {
      window.clearTimeout(timeout);
      setQuerying(false);
    }
  }

  function elapsedSeconds(startedAt) {
    var now = window.performance ? window.performance.now() : Date.now();
    return Math.max(0, (now - startedAt) / 1000);
  }

  async function readJson(response) {
    try {
      return await response.json();
    } catch (error) {
      return null;
    }
  }

  function hideEmptyState() {
    if (els.emptyState) {
      els.emptyState.style.display = "none";
    }
  }

  function updateStats(success, elapsed) {
    state.stats.total += 1;
    if (success) {
      state.stats.success += 1;
    }
    state.stats.elapsed.push(elapsed);
    els.statTotal.textContent = formatInt(state.stats.total);
    els.statSuccess.textContent = formatInt(state.stats.success);
    var total = state.stats.elapsed.reduce(function (sum, value) {
      return sum + value;
    }, 0);
    els.statElapsed.textContent = (total / state.stats.elapsed.length).toFixed(1) + "s";
  }

  function appendPending(id, question, startedAt) {
    var article = document.createElement("article");
    article.className = "record";
    article.dataset.id = String(id);
    article.innerHTML =
      questionHeaderHtml(id, question, startedAt) +
      '<div class="card result-card pending-card">' +
      '<div class="result-body">' +
      '<div class="pending-body">' +
      '<span class="spinner" aria-hidden="true"></span>' +
      "<div><strong>正在处理查询</strong>" +
      "<p>选表 → 生成 SQL → 校验 → 只读执行 → 结果汇总</p></div>" +
      "</div>" +
      "</div>" +
      "</div>";
    els.feed.appendChild(article);
    scrollToElement(article);
    return article;
  }

  function questionHeaderHtml(id, question, startedAt) {
    return (
      '<div class="record-question">' +
      '<span class="record-index">' + pad2(id) + "</span>" +
      "<p>" + escapeHtml(question) + "</p>" +
      '<time datetime="' + startedAt.toISOString() + '">' + timeLabel(startedAt) + "</time>" +
      "</div>"
    );
  }

  function statusOf(data) {
    if (data.error) {
      return { tone: "error", label: "未能完成", icon: "layui-icon-close-fill" };
    }
    if (!data.sql) {
      return { tone: "warn", label: "需要补充信息", icon: "layui-icon-tips-fill" };
    }
    if (!data.row_count) {
      return { tone: "empty", label: "无匹配数据", icon: "layui-icon-tips-fill" };
    }
    return { tone: "ok", label: "查询成功", icon: "layui-icon-ok-circle" };
  }

  function appendResult(id, question, startedAt, data, elapsed) {
    state.records.set(id, data);
    var status = statusOf(data);
    var article = document.createElement("article");
    article.className = "record";
    article.dataset.id = String(id);

    var meta = [];
    if (data.tables && data.tables.length) {
      meta.push(
        '<span class="meta">表 <code>' + escapeHtml(data.tables.join(", ")) + "</code></span>"
      );
    }
    if (data.sql) {
      meta.push(
        '<span class="meta">行数 <b>' + formatInt(data.row_count) + (data.truncated ? "+" : "") + "</b></span>"
      );
    }
    if (data.repair_attempts) {
      meta.push('<span class="meta">修复 <b>' + formatInt(data.repair_attempts) + "</b> 次</span>");
    }
    meta.push('<span class="meta">耗时 <b>' + elapsed.toFixed(1) + "s</b></span>");

    article.innerHTML =
      questionHeaderHtml(id, question, startedAt) +
      '<div class="card result-card">' +
      '<div class="result-body">' +
      '<div class="status-row">' +
      '<span class="badge is-' + status.tone + '">' +
      '<i class="layui-icon ' + status.icon + '" aria-hidden="true"></i>' +
      status.label +
      "</span>" +
      meta.join("") +
      "</div>" +
      '<div class="answer">' + renderAnswer(data.answer || "未生成答案。") + "</div>" +
      renderNotes(data) +
      renderEvidence(data.evidence) +
      renderSqlBlock(data.sql) +
      renderTableBlock(data) +
      "</div>" +
      "</div>";

    els.feed.appendChild(article);
    scrollToElement(article);
  }

  function appendFailure(id, question, startedAt, message, elapsed) {
    var article = document.createElement("article");
    article.className = "record";
    article.dataset.id = String(id);
    article.innerHTML =
      questionHeaderHtml(id, question, startedAt) +
      '<div class="card result-card">' +
      '<div class="result-body">' +
      '<div class="status-row">' +
      '<span class="badge is-error">' +
      '<i class="layui-icon layui-icon-close-fill" aria-hidden="true"></i>请求失败' +
      "</span>" +
      '<span class="meta">耗时 <b>' + elapsed.toFixed(1) + "s</b></span>" +
      "</div>" +
      '<div class="answer"><p>' + escapeHtml(message) + "</p></div>" +
      "</div>" +
      "</div>";
    els.feed.appendChild(article);
    scrollToElement(article);
  }

  function renderNotes(data) {
    var notes = [];
    if (data.error) {
      notes.push(
        '<div class="note is-error"><span class="note-label">错误</span>' + escapeHtml(data.error) + "</div>"
      );
    }
    (data.caveats || []).forEach(function (caveat) {
      if (caveat) {
        notes.push(
          '<div class="note is-warn"><span class="note-label">说明</span>' + escapeHtml(caveat) + "</div>"
        );
      }
    });
    if (!notes.length) {
      return "";
    }
    return '<div class="notes">' + notes.join("") + "</div>";
  }

  function renderEvidence(evidence) {
    var items = (evidence || []).filter(function (item) {
      return item != null && String(item).trim() !== "";
    });
    if (!items.length) {
      return "";
    }
    var shown = items.slice(0, MAX_EVIDENCE);
    var chips = shown
      .map(function (item) {
        return '<span class="evidence-item" title="' + escapeAttr(item) + '">' + escapeHtml(item) + "</span>";
      })
      .join("");
    if (items.length > shown.length) {
      chips += '<span class="evidence-item">+' + (items.length - shown.length) + "</span>";
    }
    return '<div class="evidence"><span class="note-label">依据</span>' + chips + "</div>";
  }

  function renderSqlBlock(sql) {
    if (!sql) {
      return "";
    }
    return (
      '<div class="block">' +
      '<div class="block-head"><strong>执行 SQL</strong>' +
      '<button type="button" class="link-btn" data-action="copy-sql">复制</button></div>' +
      '<pre class="code">' + escapeHtml(sql) + "</pre>" +
      "</div>"
    );
  }

  function renderTableBlock(data) {
    var columns = data.columns || [];
    var rows = data.rows || [];
    if (!columns.length || !rows.length) {
      return "";
    }

    var head = columns
      .map(function (column) {
        return "<th>" + escapeHtml(column) + "</th>";
      })
      .join("");

    var body = rows
      .map(function (row) {
        return (
          "<tr>" +
          columns
            .map(function (column, index) {
              var value = row[index];
              if (value === "" || value == null) {
                return '<td><span class="cell-null">—</span></td>';
              }
              return '<td title="' + escapeAttr(value) + '">' + escapeHtml(value) + "</td>";
            })
            .join("") +
          "</tr>"
        );
      })
      .join("");

    var note = data.truncated
      ? '<div class="table-foot-note">结果已截断，仅显示前 ' + formatInt(rows.length) + " 行。</div>"
      : "";

    return (
      '<div class="block">' +
      '<div class="block-head"><strong>查询结果<em>' +
      formatInt(data.row_count) +
      " 行</em></strong>" +
      '<button type="button" class="link-btn" data-action="export">导出 CSV</button></div>' +
      '<div class="table-scroll"><table class="data-table">' +
      "<thead><tr>" + head + "</tr></thead>" +
      "<tbody>" + body + "</tbody>" +
      "</table></div>" +
      note +
      "</div>"
    );
  }

  /* ---------- 轻量 Markdown 渲染（先转义，再生成标签） ---------- */

  function inlineMarkdown(text) {
    var safe = escapeHtml(text);
    safe = safe.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    safe = safe.replace(/`([^`]+)`/g, "<code>$1</code>");
    safe = safe.replace(/\n/g, "<br>");
    return safe;
  }

  function isTableDivider(line) {
    return /^\s*\|?[\s:|-]+\|?\s*$/.test(line) && line.indexOf("-") !== -1 && line.indexOf("|") !== -1;
  }

  function splitTableRow(line) {
    var value = line.trim();
    if (value.charAt(0) === "|") {
      value = value.slice(1);
    }
    if (value.charAt(value.length - 1) === "|") {
      value = value.slice(0, -1);
    }
    return value.split("|").map(function (cell) {
      return cell.trim();
    });
  }

  function answerTableHtml(header, rows) {
    var head = header
      .map(function (cell) {
        return "<th>" + inlineMarkdown(cell) + "</th>";
      })
      .join("");
    var body = rows
      .map(function (row) {
        return (
          "<tr>" +
          header
            .map(function (_, index) {
              return "<td>" + inlineMarkdown(row[index] == null ? "" : row[index]) + "</td>";
            })
            .join("") +
          "</tr>"
        );
      })
      .join("");
    return "<table><thead><tr>" + head + "</tr></thead><tbody>" + body + "</tbody></table>";
  }

  function renderAnswer(text) {
    var raw = String(text == null ? "" : text).replace(/\r\n/g, "\n").trim();
    if (!raw) {
      return "";
    }

    var codeBlocks = [];
    var working = raw.replace(/```[a-zA-Z0-9_-]*\n?([\s\S]*?)```/g, function (_, code) {
      var token = "__CODE_BLOCK_" + codeBlocks.length + "__";
      codeBlocks.push("<pre><code>" + escapeHtml(code.replace(/\n$/, "")) + "</code></pre>");
      return token;
    });

    var lines = working.split("\n");
    var html = [];
    var paragraph = [];
    var index = 0;

    function flushParagraph() {
      if (paragraph.length) {
        html.push("<p>" + inlineMarkdown(paragraph.join("\n")) + "</p>");
        paragraph = [];
      }
    }

    while (index < lines.length) {
      var line = lines[index];
      var trimmed = line.trim();

      if (!trimmed) {
        flushParagraph();
        index += 1;
        continue;
      }

      var codeMatch = trimmed.match(/^__CODE_BLOCK_(\d+)__$/);
      if (codeMatch) {
        flushParagraph();
        html.push(codeBlocks[Number(codeMatch[1])]);
        index += 1;
        continue;
      }

      if (trimmed.indexOf("|") !== -1 && index + 1 < lines.length && isTableDivider(lines[index + 1])) {
        flushParagraph();
        var header = splitTableRow(line);
        var rows = [];
        var cursor = index + 2;
        while (cursor < lines.length && lines[cursor].indexOf("|") !== -1 && lines[cursor].trim()) {
          rows.push(splitTableRow(lines[cursor]));
          cursor += 1;
        }
        html.push(answerTableHtml(header, rows));
        index = cursor;
        continue;
      }

      var heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
      if (heading) {
        flushParagraph();
        var level = Math.min(heading[1].length + 2, 6);
        html.push("<h" + level + ">" + inlineMarkdown(heading[2]) + "</h" + level + ">");
        index += 1;
        continue;
      }

      if (/^\s*[-*+]\s+/.test(line)) {
        flushParagraph();
        var items = [];
        while (index < lines.length && /^\s*[-*+]\s+/.test(lines[index])) {
          items.push(lines[index].replace(/^\s*[-*+]\s+/, ""));
          index += 1;
        }
        html.push(
          "<ul>" +
            items
              .map(function (item) {
                return "<li>" + inlineMarkdown(item) + "</li>";
              })
              .join("") +
            "</ul>"
        );
        continue;
      }

      if (/^\s*\d+[.、)]\s+/.test(line)) {
        flushParagraph();
        var ordered = [];
        while (index < lines.length && /^\s*\d+[.、)]\s+/.test(lines[index])) {
          ordered.push(lines[index].replace(/^\s*\d+[.、)]\s+/, ""));
          index += 1;
        }
        html.push(
          "<ol>" +
            ordered
              .map(function (item) {
                return "<li>" + inlineMarkdown(item) + "</li>";
              })
              .join("") +
            "</ol>"
        );
        continue;
      }

      paragraph.push(line);
      index += 1;
    }

    flushParagraph();
    return html.join("");
  }

  /* ---------- 复制与导出 ---------- */

  async function copyText(text, message) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        var textarea = document.createElement("textarea");
        textarea.value = text;
        textarea.setAttribute("readonly", "");
        textarea.style.position = "fixed";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand("copy");
        textarea.remove();
      }
      toast(message || "已复制", 1);
    } catch (error) {
      toast("复制失败，请手动选择文本", 2);
    }
  }

  function csvCell(value) {
    var text = value == null ? "" : String(value);
    return /[",\r\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
  }

  function exportCsv(data) {
    var columns = data.columns || [];
    var rows = data.rows || [];
    if (!columns.length) {
      return;
    }
    var lines = [columns]
      .concat(rows)
      .map(function (row) {
        return row.map(csvCell).join(",");
      })
      .join("\r\n");

    var blob = new Blob(["﻿" + lines], { type: "text/csv;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = "chinook-query-" + stamp() + ".csv";
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 1000);
    toast("CSV 已导出", 1);
  }

  /* ---------- 事件绑定 ---------- */

  function bindEvents() {
    els.form.addEventListener("submit", submitQuery);

    els.input.addEventListener("input", autosizeInput);
    els.input.addEventListener("keydown", function (event) {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        if (typeof els.form.requestSubmit === "function") {
          els.form.requestSubmit();
        } else {
          submitQuery();
        }
      }
    });

    els.btnSettings.addEventListener("click", openDrawer);
    els.btnCloseDrawer.addEventListener("click", closeDrawer);
    els.drawerMask.addEventListener("click", closeDrawer);
    els.settingsForm.addEventListener("submit", saveSettings);
    els.btnResetSettings.addEventListener("click", resetSettings);

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && els.drawer.classList.contains("is-open")) {
        closeDrawer();
      }
    });

    els.feed.addEventListener("click", function (event) {
      var button = event.target.closest("[data-action]");
      if (!button) {
        return;
      }
      var record = button.closest(".record");
      if (!record) {
        return;
      }
      var data = state.records.get(Number(record.dataset.id));
      var action = button.getAttribute("data-action");
      if (action === "copy-sql" && data && data.sql) {
        copyText(data.sql, "SQL 已复制");
      } else if (action === "export" && data) {
        exportCsv(data);
      }
    });

    if (els.linkSchema) {
      els.linkSchema.addEventListener("click", function (event) {
        if (window.getComputedStyle(document.querySelector(".rail")).display === "none") {
          event.preventDefault();
          toast("数据库概览在当前窗口宽度下已隐藏", 0);
        }
      });
    }
  }

  function init() {
    cacheElements();
    loadStoredSettings();
    bindEvents();
    renderSettingsInputs();
    renderKeyStatus();
    autosizeInput();
    loadConfig();
  }

  if (window.layui) {
    window.layui.use(["layer"], function () {
      layer = window.layui.layer;
      init();
    });
  } else {
    init();
  }
})();
