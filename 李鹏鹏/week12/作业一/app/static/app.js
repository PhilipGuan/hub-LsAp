(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const els = {
    providerSelect: $("providerSelect"),
    connPill: $("connPill"),
    connText: $("connText"),
    configNotice: $("configNotice"),
    dropzone: $("dropzone"),
    dropEmpty: $("dropEmpty"),
    previewWrap: $("previewWrap"),
    previewImage: $("previewImage"),
    previewChip: $("previewChip"),
    scanOverlay: $("scanOverlay"),
    scanText: $("scanText"),
    cameraBtn: $("cameraBtn"),
    uploadBtn: $("uploadBtn"),
    resetBtn: $("resetBtn"),
    fileInput: $("fileInput"),
    cameraInput: $("cameraInput"),
    resultCard: $("resultCard"),
    resultEmpty: $("resultEmpty"),
    resultLoading: $("resultLoading"),
    resultError: $("resultError"),
    resultContent: $("resultContent"),
    loadingTitle: $("loadingTitle"),
    loadingStep: $("loadingStep"),
    errorText: $("errorText"),
    errorRetryBtn: $("errorRetryBtn"),
    modelPill: $("modelPill"),
    riskLabel: $("riskLabel"),
    riskBadge: $("riskBadge"),
    confidenceVal: $("confidenceVal"),
    confidenceBar: $("confidenceBar"),
    resultSummary: $("resultSummary"),
    metaModel: $("metaModel"),
    metaElapsed: $("metaElapsed"),
    metaTime: $("metaTime"),
    actionBanner: $("actionBanner"),
    hazardsSection: $("hazardsSection"),
    hazardList: $("hazardList"),
    hazardCount: $("hazardCount"),
    evidenceSection: $("evidenceSection"),
    evidenceList: $("evidenceList"),
    suggestionList: $("suggestionList"),
    copyBtn: $("copyBtn"),
    reanalyzeBtn: $("reanalyzeBtn"),
    historySection: $("historySection"),
    historyList: $("historyList"),
    clearHistoryBtn: $("clearHistoryBtn"),
    cameraDialog: $("cameraDialog"),
    cameraVideo: $("cameraVideo"),
    cameraMessage: $("cameraMessage"),
    closeCameraBtn: $("closeCameraBtn"),
    switchCameraBtn: $("switchCameraBtn"),
    captureBtn: $("captureBtn"),
    fallbackCameraBtn: $("fallbackCameraBtn"),
    captureCanvas: $("captureCanvas"),
    toast: $("toast"),
  };

  const MAX_CLIENT_BYTES = 12 * 1024 * 1024;
  const MAX_HISTORY = 6;
  const CLIENT_TIMEOUT_MS = 120000;
  const LOADING_STEPS = [
    "正在提取画面结构…",
    "正在识别隐患元素…",
    "正在比对风险规则…",
    "正在生成处置建议…",
  ];

  const RISK_META = {
    高风险: { code: "high" },
    中风险: { code: "medium" },
    低风险: { code: "low" },
    无风险: { code: "none" },
    非楼道: { code: "other" },
  };

  const SEVERITY_LABEL = { high: "严重", medium: "中等", low: "轻微" };

  const state = {
    providers: [],
    file: null,
    previewUrl: null,
    result: null,
    analyzing: false,
    loadingTimer: null,
    toastTimer: null,
    stream: null,
    facingMode: "environment",
    history: [],
  };

  /* ------------------------------------------------------------- helpers */

  function riskMeta(level) {
    return RISK_META[level] || RISK_META["非楼道"];
  }

  function showToast(message, type = "info", duration = 3400) {
    window.clearTimeout(state.toastTimer);
    els.toast.textContent = message;
    els.toast.dataset.type = type;
    els.toast.hidden = false;
    state.toastTimer = window.setTimeout(() => {
      els.toast.hidden = true;
    }, duration);
  }

  function formatBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  function formatClock(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "—";
    return new Intl.DateTimeFormat("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(date);
  }

  function formatHistoryTime(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "—";
    const now = new Date();
    const sameDay =
      date.getFullYear() === now.getFullYear() &&
      date.getMonth() === now.getMonth() &&
      date.getDate() === now.getDate();
    const time = new Intl.DateTimeFormat("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date);
    if (sameDay) return `今天 ${time}`;
    const day = new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit",
      day: "2-digit",
    }).format(date);
    return `${day} ${time}`;
  }

  function selectedProvider() {
    return state.providers.find((item) => item.id === els.providerSelect.value) || null;
  }

  function isImageFile(file) {
    const type = (file.type || "").toLowerCase();
    if (type.startsWith("image/")) return true;
    return /\.(jpe?g|png|webp|gif|bmp|heic|heif|avif)$/i.test(file.name || "");
  }

  function setConnState(mode, text) {
    els.connPill.classList.remove("is-loading", "is-error");
    if (mode === "loading") els.connPill.classList.add("is-loading");
    if (mode === "error") els.connPill.classList.add("is-error");
    els.connText.textContent = text;
  }

  /* ------------------------------------------------------------ providers */

  async function loadProviders() {
    setConnState("loading", "连接中");
    try {
      const response = await fetch("/api/providers", {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      state.providers = Array.isArray(data.providers) ? data.providers : [];

      els.providerSelect.replaceChildren();
      for (const provider of state.providers) {
        const option = document.createElement("option");
        option.value = provider.id;
        option.textContent = provider.configured
          ? provider.label
          : `${provider.label}（未配置）`;
        option.disabled = !provider.configured;
        option.title = provider.configured
          ? `${provider.description} · ${provider.model}`
          : `请在 .env 中配置 ${provider.label} 的 API Key`;
        els.providerSelect.append(option);
      }

      const preferred =
        state.providers.find(
          (item) => item.id === data.default && item.configured
        ) || state.providers.find((item) => item.configured);

      const anyConfigured = Boolean(preferred);
      els.providerSelect.disabled = !anyConfigured;
      els.configNotice.hidden = anyConfigured;

      if (preferred) {
        els.providerSelect.value = preferred.id;
        els.providerSelect.title = `${preferred.description} · ${preferred.model}`;
        setConnState("ok", "模型就绪");
      } else {
        setConnState("error", "待配置");
        els.connText.textContent = "待配置";
      }
    } catch (error) {
      console.error(error);
      setConnState("error", "后端离线");
      els.providerSelect.disabled = true;
      els.providerSelect.replaceChildren(new Option("后端未连接", ""));
      showToast("无法连接后端服务，请确认已运行 python run.py。", "error", 5200);
    }
  }

  /* -------------------------------------------------------------- results */

  function setResultState(kind) {
    els.resultEmpty.hidden = kind !== "empty";
    els.resultLoading.hidden = kind !== "loading";
    els.resultError.hidden = kind !== "error";
    els.resultContent.hidden = kind !== "content";
    els.resultCard.setAttribute("aria-busy", kind === "loading" ? "true" : "false");

    if (kind === "empty") {
      els.resultCard.removeAttribute("data-risk");
      els.modelPill.hidden = true;
    }
  }

  function setBusy(busy) {
    state.analyzing = busy;
    els.cameraBtn.disabled = busy;
    els.uploadBtn.disabled = busy;
    els.resetBtn.disabled = busy;
    els.reanalyzeBtn.disabled = busy;
    els.errorRetryBtn.disabled = busy;
    if (busy) {
      els.providerSelect.disabled = true;
    } else {
      els.providerSelect.disabled = !state.providers.some((item) => item.configured);
    }
  }

  function startLoadingSteps() {
    stopLoadingSteps();
    let index = 0;
    els.loadingStep.textContent = LOADING_STEPS[0];
    state.loadingTimer = window.setInterval(() => {
      index = (index + 1) % LOADING_STEPS.length;
      els.loadingStep.textContent = LOADING_STEPS[index];
    }, 1900);
  }

  function stopLoadingSteps() {
    if (state.loadingTimer) {
      window.clearInterval(state.loadingTimer);
      state.loadingTimer = null;
    }
  }

  function setPreview(file) {
    if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
    state.file = file;
    state.previewUrl = URL.createObjectURL(file);
    els.previewImage.src = state.previewUrl;
    els.previewChip.textContent = `${file.name || "现场照片"} · ${formatBytes(file.size)}`;
    els.dropEmpty.hidden = true;
    els.previewWrap.hidden = false;
    els.dropzone.classList.add("has-preview");
    els.scanOverlay.hidden = true;
    els.resetBtn.hidden = false;
  }

  function clearPreview() {
    if (state.previewUrl) {
      URL.revokeObjectURL(state.previewUrl);
      state.previewUrl = null;
    }
    state.file = null;
    state.result = null;
    els.previewImage.removeAttribute("src");
    els.previewWrap.hidden = true;
    els.dropEmpty.hidden = false;
    els.dropzone.classList.remove("has-preview");
    els.resetBtn.hidden = true;
    els.scanOverlay.hidden = true;
    els.modelPill.hidden = true;
    setResultState("empty");
  }

  function renderResult(data) {
    const meta = riskMeta(data.risk_level);
    els.resultCard.dataset.risk = meta.code;

    els.riskLabel.textContent = data.risk_level;
    els.resultSummary.textContent = data.summary;

    const confidence = Number(data.confidence);
    const hasConfidence = Number.isFinite(confidence);
    els.confidenceVal.textContent = hasConfidence
      ? `${Math.round(Math.min(1, Math.max(0, confidence)) * 100)}%`
      : "—";
    els.confidenceBar.style.width = "0%";
    if (hasConfidence) {
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(() => {
          els.confidenceBar.style.width = `${Math.round(
            Math.min(1, Math.max(0, confidence)) * 100
          )}%`;
        });
      });
    }

    els.metaModel.textContent = data.provider_label || data.provider || "视觉模型";
    els.metaElapsed.textContent = `耗时 ${(Number(data.elapsed_ms || 0) / 1000).toFixed(1)} s`;
    els.metaTime.textContent = formatClock(data.timestamp);

    els.modelPill.textContent = `${data.provider_label || data.provider} · ${data.model}`;
    els.modelPill.title = `${data.provider_label || data.provider} · ${data.model}`;
    els.modelPill.hidden = false;

    const showBanner = Boolean(data.need_action);
    els.actionBanner.hidden = !showBanner;
    if (showBanner) {
      const bannerText = els.actionBanner.querySelector("span");
      if (data.risk_level === "高风险") {
        bannerText.textContent = "建议立即处置：移除起火源并清理现场，必要时联系物业或消防。";
      } else if (data.risk_level === "中风险") {
        bannerText.textContent = "建议尽快处置：清理可燃堆物，恢复疏散通道畅通。";
      } else {
        bannerText.textContent = "建议尽快处置，避免隐患升级。";
      }
    }

    const hazards = Array.isArray(data.hazards) ? data.hazards : [];
    els.hazardList.replaceChildren();
    els.hazardsSection.hidden = data.risk_level === "非楼道";
    els.hazardCount.textContent = String(hazards.length);

    if (!hazards.length && data.risk_level !== "非楼道") {
      const none = document.createElement("div");
      none.className = "hazard-none";
      none.textContent =
        data.risk_level === "无风险"
          ? "未发现明显消防隐患，请继续保持通道畅通。"
          : "未定位到具体隐患位置，请结合画面摘要综合判断。";
      els.hazardList.append(none);
    } else {
      for (const hazard of hazards) {
        const item = document.createElement("div");
        item.className = "hazard-item";
        item.dataset.severity = hazard.severity || "low";

        const top = document.createElement("div");
        top.className = "hazard-top";

        const category = document.createElement("span");
        category.className = "hazard-cat";
        category.textContent = hazard.category || "其他隐患";

        const severity = document.createElement("span");
        severity.className = "severity-chip";
        severity.textContent = SEVERITY_LABEL[hazard.severity] || "轻微";

        top.append(category, severity);

        const description = document.createElement("p");
        description.textContent = hazard.description || "";

        item.append(top, description);
        els.hazardList.append(item);
      }
    }

    const evidence = Array.isArray(data.evidence) ? data.evidence : [];
    els.evidenceList.replaceChildren();
    els.evidenceSection.hidden = evidence.length === 0;
    for (const text of evidence) {
      const item = document.createElement("li");
      item.textContent = text;
      els.evidenceList.append(item);
    }

    const suggestions = Array.isArray(data.suggestions) ? data.suggestions : [];
    els.suggestionList.replaceChildren();
    for (const text of suggestions) {
      const item = document.createElement("li");
      item.textContent = text;
      els.suggestionList.append(item);
    }
  }

  /* --------------------------------------------------------------- history */

  function addHistory(file, data) {
    const entry = {
      id: data.analysis_id || `${Date.now()}`,
      file,
      thumbUrl: URL.createObjectURL(file),
      data,
    };
    state.history.unshift(entry);
    while (state.history.length > MAX_HISTORY) {
      const removed = state.history.pop();
      URL.revokeObjectURL(removed.thumbUrl);
    }
    renderHistory();
  }

  function renderHistory() {
    els.historyList.replaceChildren();
    for (const entry of state.history) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "history-item";
      button.setAttribute(
        "aria-label",
        `查看研判记录：${entry.data.risk_level}，${entry.data.summary}`
      );

      const thumb = document.createElement("div");
      thumb.className = "history-thumb";

      const image = document.createElement("img");
      image.src = entry.thumbUrl;
      image.alt = "";

      const risk = document.createElement("span");
      risk.className = "history-risk";
      risk.dataset.risk = riskMeta(entry.data.risk_level).code;
      const dot = document.createElement("i");
      risk.append(dot, document.createTextNode(entry.data.risk_level));

      thumb.append(image, risk);

      const info = document.createElement("div");
      info.className = "history-info";
      const summary = document.createElement("strong");
      summary.textContent = entry.data.summary;
      const time = document.createElement("span");
      time.textContent = `${formatHistoryTime(entry.data.timestamp)} · ${entry.data.provider_label}`;
      info.append(summary, time);

      button.append(thumb, info);
      button.addEventListener("click", () => restoreHistory(entry));
      els.historyList.append(button);
    }
    els.historySection.hidden = state.history.length === 0;
  }

  function restoreHistory(entry) {
    if (state.analyzing) return;
    setPreview(entry.file);
    state.result = entry.data;
    renderResult(entry.data);
    setResultState("content");
    window.scrollTo({
      top: Math.max(0, els.resultCard.getBoundingClientRect().top + window.scrollY - 96),
      behavior: "smooth",
    });
  }

  function clearHistory() {
    for (const entry of state.history) URL.revokeObjectURL(entry.thumbUrl);
    state.history = [];
    renderHistory();
  }

  /* --------------------------------------------------------------- analyze */

  async function analyze(file = state.file) {
    if (!file || state.analyzing) return;

    const provider = selectedProvider();
    if (!provider || !provider.configured) {
      showToast("请先在 .env 中配置模型 API Key，并重启服务。", "error", 4800);
      els.configNotice.hidden = false;
      return;
    }

    setBusy(true);
    setResultState("loading");
    els.scanOverlay.hidden = false;
    els.scanText.textContent = "AI 正在研判";
    startLoadingSteps();

    const controller = new AbortController();
    const timeout = window.setTimeout(
      () => controller.abort(),
      CLIENT_TIMEOUT_MS
    );

    try {
      const form = new FormData();
      form.append("file", file, file.name || "corridor.jpg");
      form.append("provider", provider.id);

      const response = await fetch("/api/analyze", {
        method: "POST",
        body: form,
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });

      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          typeof payload.detail === "string"
            ? payload.detail
            : `识别失败（HTTP ${response.status}）`
        );
      }

      state.result = payload;
      renderResult(payload);
      setResultState("content");
      addHistory(file, payload);
    } catch (error) {
      console.error(error);
      let message = "识别失败，请重试。";
      if (error.name === "AbortError") {
        message = "识别超时，请检查网络或稍后重试。";
      } else if (error instanceof TypeError) {
        message = "无法连接后端服务，请确认服务仍在运行。";
      } else if (error.message) {
        message = error.message;
      }
      els.errorText.textContent = message;
      els.resultCard.removeAttribute("data-risk");
      setResultState("error");
    } finally {
      window.clearTimeout(timeout);
      stopLoadingSteps();
      els.scanOverlay.hidden = true;
      setBusy(false);
    }
  }

  function handleFile(file) {
    if (!file) return;
    if (state.analyzing) {
      showToast("正在识别当前照片，请稍候。", "info");
      return;
    }
    if (!isImageFile(file)) {
      showToast("请选择图片文件（JPG、PNG、WebP 或 HEIC）。", "error");
      return;
    }
    if (file.size > MAX_CLIENT_BYTES) {
      showToast("图片超过 12 MB，请压缩或重新拍摄。", "error", 4200);
      return;
    }
    setPreview(file);
    analyze(file);
  }

  /* ---------------------------------------------------------------- camera */

  function cameraApiAvailable() {
    return Boolean(
      window.isSecureContext !== false &&
        navigator.mediaDevices &&
        navigator.mediaDevices.getUserMedia
    );
  }

  function openNativeCamera(message) {
    if (message) showToast(message, "info", 4200);
    els.cameraInput.value = "";
    els.cameraInput.click();
  }

  async function openCamera() {
    if (state.analyzing) return;
    if (!cameraApiAvailable()) {
      openNativeCamera(
        "当前环境不允许网页直接调用摄像头，已切换为系统相机。"
      );
      return;
    }

    if (!els.cameraDialog.open) {
      if (typeof els.cameraDialog.showModal === "function") {
        els.cameraDialog.showModal();
      } else {
        els.cameraDialog.setAttribute("open", "");
      }
    }
    await startCamera();
  }

  async function startCamera() {
    stopCamera();
    els.captureBtn.disabled = true;
    els.cameraMessage.hidden = false;
    els.cameraMessage.textContent = "正在启动摄像头…";
    els.cameraVideo.classList.toggle("is-mirrored", state.facingMode === "user");

    try {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        throw new DOMException("Camera API unavailable", "NotSupportedError");
      }
      try {
        state.stream = await navigator.mediaDevices.getUserMedia({
          audio: false,
          video: {
            facingMode: { ideal: state.facingMode },
            width: { ideal: 1920 },
            height: { ideal: 1440 },
          },
        });
      } catch (error) {
        if (
          error.name === "OverconstrainedError" ||
          error.name === "NotFoundError"
        ) {
          state.stream = await navigator.mediaDevices.getUserMedia({
            audio: false,
            video: true,
          });
        } else {
          throw error;
        }
      }

      els.cameraVideo.srcObject = state.stream;
      await els.cameraVideo.play().catch(() => {});
      els.cameraMessage.hidden = true;
      els.captureBtn.disabled = false;
    } catch (error) {
      console.warn(error);
      els.cameraMessage.hidden = false;
      els.cameraMessage.textContent = cameraErrorText(error);
      els.captureBtn.disabled = true;
    }
  }

  function cameraErrorText(error) {
    switch (error.name) {
      case "NotAllowedError":
      case "SecurityError":
        return "摄像头权限被拒绝。请在浏览器设置中允许访问，或点击「系统相机」。";
      case "NotFoundError":
        return "未检测到可用摄像头，请点击「系统相机」或改用上传图片。";
      case "NotReadableError":
        return "摄像头被其他应用占用，请关闭占用程序后重试。";
      case "NotSupportedError":
        return "当前浏览器不支持网页内摄像头，请点击「系统相机」。";
      default:
        return "摄像头启动失败，请点击「系统相机」或改用上传图片。";
    }
  }

  function stopCamera() {
    if (state.stream) {
      for (const track of state.stream.getTracks()) track.stop();
      state.stream = null;
    }
    els.cameraVideo.srcObject = null;
    els.captureBtn.disabled = true;
  }

  function closeCamera() {
    stopCamera();
    if (els.cameraDialog.open) {
      if (typeof els.cameraDialog.close === "function") {
        els.cameraDialog.close();
      } else {
        els.cameraDialog.removeAttribute("open");
      }
    }
  }

  function capturePhoto() {
    const video = els.cameraVideo;
    if (!state.stream || !video.videoWidth || !video.videoHeight) {
      showToast("画面尚未准备好，请稍候再拍。", "info");
      return;
    }

    const maxSide = 2000;
    const scale = Math.min(1, maxSide / Math.max(video.videoWidth, video.videoHeight));
    const canvas = els.captureCanvas;
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);
    const context = canvas.getContext("2d");
    context.drawImage(video, 0, 0, canvas.width, canvas.height);

    canvas.toBlob(
      (blob) => {
        if (!blob) {
          showToast("拍照失败，请重试。", "error");
          return;
        }
        const stamp = new Date()
          .toISOString()
          .replace(/[-:T]/g, "")
          .slice(0, 14);
        const file = new File([blob], `corridor-${stamp}.jpg`, {
          type: "image/jpeg",
          lastModified: Date.now(),
        });
        closeCamera();
        handleFile(file);
      },
      "image/jpeg",
      0.9
    );
  }

  async function switchCamera() {
    state.facingMode = state.facingMode === "environment" ? "user" : "environment";
    await startCamera();
  }

  /* ----------------------------------------------------------------- copy */

  function buildReport(data) {
    const lines = [
      "楼道消防安全研判",
      `风险等级：${data.risk_level}（置信度 ${Math.round(
        Math.min(1, Math.max(0, Number(data.confidence) || 0)) * 100
      )}%）`,
      `画面描述：${data.summary}`,
    ];
    if (data.hazards && data.hazards.length) {
      lines.push("识别隐患：");
      data.hazards.forEach((hazard, index) => {
        lines.push(
          `${index + 1}. [${hazard.category}] ${hazard.description}（${
            SEVERITY_LABEL[hazard.severity] || "轻微"
          }）`
        );
      });
    }
    if (data.evidence && data.evidence.length) {
      lines.push("研判依据：");
      data.evidence.forEach((text) => lines.push(`- ${text}`));
    }
    if (data.suggestions && data.suggestions.length) {
      lines.push("处置建议：");
      data.suggestions.forEach((text, index) => lines.push(`${index + 1}. ${text}`));
    }
    lines.push(
      `模型：${data.provider_label} · ${data.model}`,
      `时间：${formatClock(data.timestamp)}`,
      "提示：本结果由 AI 辅助研判，不替代现场消防检查。"
    );
    return lines.join("\n");
  }

  async function copyReport() {
    if (!state.result) return;
    const text = buildReport(state.result);
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        const area = document.createElement("textarea");
        area.value = text;
        area.setAttribute("readonly", "");
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.append(area);
        area.select();
        document.execCommand("copy");
        area.remove();
      }
      showToast("研判结果已复制到剪贴板。", "success");
    } catch (error) {
      console.error(error);
      showToast("复制失败，请手动选择文本。", "error");
    }
  }

  /* ---------------------------------------------------------------- events */

  function bindEvents() {
    els.uploadBtn.addEventListener("click", () => {
      if (state.analyzing) return;
      els.fileInput.value = "";
      els.fileInput.click();
    });

    els.cameraBtn.addEventListener("click", openCamera);

    els.fileInput.addEventListener("change", (event) => {
      const file = event.target.files && event.target.files[0];
      event.target.value = "";
      if (file) handleFile(file);
    });

    els.cameraInput.addEventListener("change", (event) => {
      const file = event.target.files && event.target.files[0];
      event.target.value = "";
      if (file) handleFile(file);
    });

    els.resetBtn.addEventListener("click", () => {
      if (!state.analyzing) clearPreview();
    });

    els.reanalyzeBtn.addEventListener("click", () => {
      if (state.file) analyze(state.file);
    });

    els.errorRetryBtn.addEventListener("click", () => {
      if (state.file) analyze(state.file);
    });

    els.copyBtn.addEventListener("click", copyReport);
    els.clearHistoryBtn.addEventListener("click", clearHistory);

    els.providerSelect.addEventListener("change", () => {
      const provider = selectedProvider();
      if (provider) {
        els.providerSelect.title = `${provider.description} · ${provider.model}`;
      }
      if (state.file && state.result && !state.analyzing) {
        showToast("模型已切换，点击「用当前模型重新识别」查看新结果。", "info", 4200);
      }
    });

    els.dropzone.addEventListener("click", () => {
      if (state.analyzing) return;
      els.fileInput.value = "";
      els.fileInput.click();
    });

    els.dropzone.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        if (!state.analyzing) {
          els.fileInput.value = "";
          els.fileInput.click();
        }
      }
    });

    ["dragenter", "dragover"].forEach((type) => {
      els.dropzone.addEventListener(type, (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (!state.analyzing) els.dropzone.classList.add("dragover");
      });
    });

    ["dragleave", "drop"].forEach((type) => {
      els.dropzone.addEventListener(type, (event) => {
        event.preventDefault();
        event.stopPropagation();
        els.dropzone.classList.remove("dragover");
      });
    });

    els.dropzone.addEventListener("drop", (event) => {
      if (state.analyzing) return;
      const file = event.dataTransfer && event.dataTransfer.files[0];
      if (file) handleFile(file);
    });

    ["dragover", "drop"].forEach((type) => {
      window.addEventListener(type, (event) => event.preventDefault());
    });

    els.closeCameraBtn.addEventListener("click", closeCamera);
    els.captureBtn.addEventListener("click", capturePhoto);
    els.switchCameraBtn.addEventListener("click", switchCamera);
    els.fallbackCameraBtn.addEventListener("click", () => {
      closeCamera();
      openNativeCamera("已切换为系统相机。");
    });
    els.cameraDialog.addEventListener("close", stopCamera);
    els.cameraDialog.addEventListener("click", (event) => {
      if (event.target === els.cameraDialog) closeCamera();
    });

    window.addEventListener("pagehide", stopCamera);
    window.addEventListener("pageshow", (event) => {
      if (event.persisted && els.cameraDialog.open) startCamera();
    });
  }

  function init() {
    bindEvents();
    setResultState("empty");
    loadProviders();
  }

  init();
})();
