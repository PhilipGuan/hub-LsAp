(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const el = {
    statusPill: $("statusPill"),
    statusText: $("statusText"),
    insecureBanner: $("insecureBanner"),
    maxSize: $("maxSize"),

    tabs: Array.from(document.querySelectorAll(".tab")),
    paneCamera: $("paneCamera"),
    paneUpload: $("paneUpload"),
    cameraStage: $("cameraStage"),
    video: $("video"),
    cameraPlaceholder: $("cameraPlaceholder"),
    cameraHint: $("cameraHint"),
    switchCamera: $("switchCamera"),
    captureBtn: $("captureBtn"),

    dropzone: $("dropzone"),
    fileInput: $("fileInput"),

    preview: $("preview"),
    previewImg: $("previewImg"),
    resetBtn: $("resetBtn"),
    analyzeBtn: $("analyzeBtn"),

    resultEmpty: $("resultEmpty"),
    resultLoading: $("resultLoading"),
    resultError: $("resultError"),
    errorText: $("errorText"),
    retryBtn: $("retryBtn"),
    resultBody: $("resultBody"),
    verdict: $("verdict"),
    levelText: $("levelText"),
    levelHint: $("levelHint"),
    confidenceText: $("confidenceText"),
    confidenceBar: $("confidenceBar"),
    evidenceList: $("evidenceList"),
    suggestionText: $("suggestionText"),
    meta: $("meta"),
    againBtn: $("againBtn"),

    history: $("history"),
    historyList: $("historyList"),
  };

  const LEVELS = {
    "高风险": { hint: "存在可能直接起火的元素，请立即处置并上报", color: "#d92d20" },
    "中风险": { hint: "可燃物大量堆积，火势易蔓延，建议当日清理", color: "#dc6803" },
    "低风险": { hint: "存在少量堆物，建议提醒住户及时清理", color: "#a16207" },
    "无风险": { hint: "楼道干净、通道畅通，保持日常巡查", color: "#067647" },
    "非楼道": { hint: "画面不是楼道场景，请重新拍摄", color: "#475467" },
  };

  const state = {
    tab: "camera",
    facing: "environment",
    stream: null,
    blob: null,
    filename: null,
    previewUrl: null,
    busy: false,
    maxBytes: 10 * 1024 * 1024,
    history: [],
  };

  /* ------------------------------ 状态提示 ------------------------------ */

  function setStatus(kind, text, title) {
    el.statusPill.classList.remove("is-ok", "is-warn", "is-bad");
    if (kind) el.statusPill.classList.add(kind);
    el.statusText.textContent = text;
    if (title) el.statusPill.title = title;
  }

  async function loadHealth() {
    try {
      const res = await fetch("/api/health", { cache: "no-store" });
      if (!res.ok) throw new Error("health check failed");
      const health = await res.json();
      state.maxBytes = (health.max_image_mb || 10) * 1024 * 1024;
      el.maxSize.textContent = health.max_image_mb || 10;

      if (health.mock) {
        setStatus("is-warn", "演示模式", "MOCK_MODE=1，返回的是规则生成的示例结果");
      } else if (!health.key_configured) {
        setStatus("is-bad", "未配置 API Key", "请在 app/.env 中填写 DASHSCOPE_API_KEY");
      } else {
        setStatus("is-ok", health.model, health.base_url);
      }
    } catch (err) {
      setStatus("is-bad", "服务不可用");
    }
  }

  /* ------------------------------ 摄像头 ------------------------------ */

  function cameraError(message) {
    el.cameraStage.classList.remove("is-live");
    el.cameraPlaceholder.classList.add("is-error");
    el.cameraHint.textContent = message;
    el.captureBtn.disabled = true;
  }

  async function startCamera() {
    if (state.stream || state.tab !== "camera" || state.blob) return;

    if (!window.isSecureContext) {
      cameraError("当前页面不是 HTTPS 安全连接，浏览器禁止调用摄像头。请用 run.sh 以 https 启动后访问。");
      return;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      cameraError("当前浏览器不支持摄像头调用，请改用「上传图片」。");
      return;
    }

    el.cameraPlaceholder.classList.remove("is-error");
    el.cameraHint.textContent = "正在打开摄像头…";
    el.captureBtn.disabled = true;

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: {
          facingMode: { ideal: state.facing },
          width: { ideal: 1920 },
          height: { ideal: 1440 },
        },
      });
      state.stream = stream;
      el.video.srcObject = stream;
      await el.video.play().catch(() => {});
      el.cameraStage.classList.add("is-live");
      el.captureBtn.disabled = false;
    } catch (err) {
      const name = err && err.name;
      if (name === "NotAllowedError" || name === "SecurityError") {
        cameraError("摄像头权限被拒绝。请在浏览器地址栏的权限设置中允许使用摄像头，然后点击这里重试。");
      } else if (name === "NotFoundError" || name === "DevicesNotFoundError") {
        cameraError("没有检测到摄像头，请改用「上传图片」。");
      } else if (name === "NotReadableError") {
        cameraError("摄像头被其他应用占用，请关闭占用程序后点击这里重试。");
      } else if (name === "OverconstrainedError") {
        cameraError("无法使用当前镜头，请点击「切换镜头」后重试。");
      } else {
        cameraError("无法打开摄像头，请检查浏览器权限，或改用「上传图片」。");
      }
    }
  }

  function stopCamera() {
    if (!state.stream) return;
    state.stream.getTracks().forEach((track) => track.stop());
    state.stream = null;
    el.video.srcObject = null;
    el.cameraStage.classList.remove("is-live");
    el.captureBtn.disabled = true;
  }

  function capturePhoto() {
    if (!state.stream) return;
    const width = el.video.videoWidth;
    const height = el.video.videoHeight;
    if (!width || !height) {
      cameraError("摄像头还没有准备好，请稍等一下再试。");
      return;
    }

    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    canvas.getContext("2d").drawImage(el.video, 0, 0, width, height);

    el.cameraStage.classList.remove("is-flash");
    void el.cameraStage.offsetWidth;
    el.cameraStage.classList.add("is-flash");

    canvas.toBlob(
      (blob) => {
        if (blob) setImage(blob, "camera-" + Date.now() + ".jpg");
      },
      "image/jpeg",
      0.92
    );
  }

  /* ------------------------------ 选择图片 ------------------------------ */

  function setImage(blob, filename) {
    if (!blob) return;
    if (blob.size > state.maxBytes) {
      showError("图片超过 " + Math.round(state.maxBytes / 1024 / 1024) + "MB，请压缩后重试。");
      return;
    }

    if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
    state.blob = blob;
    state.filename = filename || "photo.jpg";
    state.previewUrl = URL.createObjectURL(blob);
    el.previewImg.src = state.previewUrl;

    stopCamera();
    applyTabVisibility();
    resetResultPanel();
  }

  function clearSelection() {
    if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
    state.blob = null;
    state.filename = null;
    state.previewUrl = null;
    el.previewImg.removeAttribute("src");
    applyTabVisibility();
    resetResultPanel();
  }

  function applyTabVisibility() {
    const showCamera = !state.blob && state.tab === "camera";
    const showUpload = !state.blob && state.tab === "upload";

    el.paneCamera.hidden = !showCamera;
    el.paneUpload.hidden = !showUpload;
    el.preview.hidden = !state.blob;

    el.tabs.forEach((tab) => {
      const active = tab.dataset.tab === state.tab;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });

    if (showCamera) startCamera();
    else stopCamera();
  }

  function switchTab(name) {
    if (name === state.tab && !state.blob) return;
    state.tab = name;
    if (state.blob) clearSelection();
    else applyTabVisibility();
  }

  /* ------------------------------ 结果渲染 ------------------------------ */

  function resetResultPanel() {
    el.resultEmpty.hidden = false;
    el.resultLoading.hidden = true;
    el.resultError.hidden = true;
    el.resultBody.hidden = true;
  }

  function showLoading() {
    el.resultEmpty.hidden = true;
    el.resultError.hidden = true;
    el.resultBody.hidden = true;
    el.resultLoading.hidden = false;
  }

  function showError(message) {
    el.resultEmpty.hidden = true;
    el.resultLoading.hidden = true;
    el.resultBody.hidden = true;
    el.resultError.hidden = false;
    el.errorText.textContent = message;
  }

  function renderResult(data) {
    const level = LEVELS[data.risk_level] ? data.risk_level : "非楼道";
    const meta = LEVELS[level];

    el.resultEmpty.hidden = true;
    el.resultLoading.hidden = true;
    el.resultError.hidden = true;
    el.resultBody.hidden = false;
    el.resultBody.dataset.level = level;

    el.levelText.textContent = level;
    el.levelHint.textContent = meta.hint;

    const confidence = Math.max(0, Math.min(1, Number(data.confidence) || 0));
    const percent = Math.round(confidence * 100);
    el.confidenceText.textContent = percent + "%";
    el.confidenceBar.style.width = "0%";
    void el.confidenceBar.offsetWidth; // 强制回流，保证宽度变化有过渡动画
    el.confidenceBar.style.width = percent + "%";

    el.evidenceList.innerHTML = "";
    (Array.isArray(data.evidence) ? data.evidence : []).forEach((item) => {
      const li = document.createElement("li");
      li.textContent = String(item);
      el.evidenceList.appendChild(li);
    });
    if (!el.evidenceList.children.length) {
      const li = document.createElement("li");
      li.textContent = "画面信息有限，未提取到明确的隐患特征。";
      el.evidenceList.appendChild(li);
    }

    el.suggestionText.textContent = data.suggestion || "请上传更清晰的楼道照片后重新识别。";

    el.meta.innerHTML = "";
    addMeta("模型 " + (data.model || "未知"));
    if (data.elapsed_ms) addMeta("耗时 " + data.elapsed_ms + " ms");
    if (data.mock) addMeta("演示数据", true);
  }

  function addMeta(text, isMock) {
    const span = document.createElement("span");
    span.textContent = text;
    if (isMock) span.className = "is-mock";
    el.meta.appendChild(span);
  }

  /* ------------------------------ 识别请求 ------------------------------ */

  async function analyze() {
    if (!state.blob || state.busy) return;

    state.busy = true;
    el.analyzeBtn.disabled = true;
    el.resetBtn.disabled = true;
    el.analyzeBtn.textContent = "识别中…";
    showLoading();
    scrollResultIntoView();

    const form = new FormData();
    form.append("file", state.blob, state.filename || "photo.jpg");

    try {
      const res = await fetch("/api/analyze", { method: "POST", body: form });
      let payload = null;
      try {
        payload = await res.json();
      } catch (err) {
        payload = null;
      }

      if (!res.ok) {
        throw new Error((payload && payload.detail) || "服务返回错误（" + res.status + "）");
      }

      renderResult(payload);
      pushHistory(state.blob, payload);
      scrollResultIntoView();
    } catch (err) {
      const message =
        err && err.message === "Failed to fetch"
          ? "无法连接服务器，请检查网络或服务是否仍在运行。"
          : (err && err.message) || "识别失败，请稍后重试。";
      showError(message);
      scrollResultIntoView();
    } finally {
      state.busy = false;
      el.analyzeBtn.disabled = false;
      el.resetBtn.disabled = false;
      el.analyzeBtn.textContent = "开始识别";
    }
  }

  function scrollResultIntoView() {
    if (window.matchMedia("(max-width: 860px)").matches) {
      const card = document.querySelector(".result-card");
      if (card) card.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  /* ------------------------------ 会话记录 ------------------------------ */

  function pushHistory(blob, data) {
    const url = URL.createObjectURL(blob);
    const now = new Date();
    const time = now.toTimeString().slice(0, 8);

    state.history.unshift({ blob, url, data, time });
    if (state.history.length > 8) {
      const removed = state.history.pop();
      URL.revokeObjectURL(removed.url);
    }
    renderHistory();
  }

  function renderHistory() {
    if (!state.history.length) {
      el.history.hidden = true;
      return;
    }
    el.history.hidden = false;
    el.historyList.innerHTML = "";

    state.history.forEach((entry) => {
      const level = LEVELS[entry.data.risk_level] ? entry.data.risk_level : "非楼道";
      const button = document.createElement("button");
      button.type = "button";
      button.className = "history-item";
      button.style.setProperty("--lv-color", LEVELS[level].color);
      button.title = "查看这次识别结果";

      const img = document.createElement("img");
      img.src = entry.url;
      img.alt = "";

      const body = document.createElement("div");
      body.className = "hi-body";

      const levelText = document.createElement("span");
      levelText.className = "hi-level";
      levelText.textContent = level;

      const timeText = document.createElement("span");
      timeText.className = "hi-time";
      timeText.textContent = entry.time.slice(0, 5);

      body.append(levelText, timeText);
      button.append(img, body);
      button.addEventListener("click", () => showHistoryEntry(entry));
      el.historyList.appendChild(button);
    });
  }

  function showHistoryEntry(entry) {
    if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
    state.blob = entry.blob;
    state.filename = "photo.jpg";
    state.previewUrl = URL.createObjectURL(entry.blob);
    el.previewImg.src = state.previewUrl;

    stopCamera();
    applyTabVisibility();
    renderResult(entry.data);
    scrollResultIntoView();
  }

  /* ------------------------------ 事件绑定 ------------------------------ */

  el.tabs.forEach((tab) => {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  });

  el.captureBtn.addEventListener("click", capturePhoto);
  el.switchCamera.addEventListener("click", () => {
    state.facing = state.facing === "environment" ? "user" : "environment";
    stopCamera();
    startCamera();
  });
  el.cameraPlaceholder.addEventListener("click", () => {
    if (!state.stream) startCamera();
  });

  el.fileInput.addEventListener("change", (event) => {
    const file = event.target.files && event.target.files[0];
    if (file) setImage(file, file.name);
    event.target.value = "";
  });

  ["dragenter", "dragover"].forEach((name) => {
    el.dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      el.dropzone.classList.add("is-drag");
    });
  });
  ["dragleave", "drop"].forEach((name) => {
    el.dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      el.dropzone.classList.remove("is-drag");
    });
  });
  el.dropzone.addEventListener("drop", (event) => {
    const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
    if (file) setImage(file, file.name);
  });

  window.addEventListener("paste", (event) => {
    const items = event.clipboardData && event.clipboardData.items;
    if (!items) return;
    for (const item of items) {
      if (item.type && item.type.startsWith("image/")) {
        const file = item.getAsFile();
        if (file) {
          event.preventDefault();
          state.tab = "upload";
          setImage(file, "pasted.png");
        }
        break;
      }
    }
  });

  el.analyzeBtn.addEventListener("click", analyze);
  el.resetBtn.addEventListener("click", clearSelection);
  el.retryBtn.addEventListener("click", analyze);
  el.againBtn.addEventListener("click", () => {
    clearSelection();
    window.scrollTo({ top: 0, behavior: "smooth" });
  });

  window.addEventListener("beforeunload", () => {
    if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
    state.history.forEach((entry) => URL.revokeObjectURL(entry.url));
  });

  /* ------------------------------ 初始化 ------------------------------ */

  if (!window.isSecureContext) {
    el.insecureBanner.hidden = false;
  }

  loadHealth();
  applyTabVisibility();
})();
