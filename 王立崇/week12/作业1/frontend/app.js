"use strict";

/* ---------------- 风险等级元信息 ---------------- */
const RISK_META = {
  high: { label: "高风险", icon: "🚨", color: "#dc2626", bg: "#fef2f2" },
  medium: { label: "中风险", icon: "⚠️", color: "#ea580c", bg: "#fff7ed" },
  low: { label: "低风险", icon: "⚠️", color: "#ca8a04", bg: "#fefce8" },
  none: { label: "无风险", icon: "✅", color: "#16a34a", bg: "#f0fdf4" },
  non_corridor: { label: "非楼道", icon: "🚫", color: "#6b7280", bg: "#f3f4f6" },
  unknown: { label: "未知", icon: "❓", color: "#6b7280", bg: "#f3f4f6" },
};

/* ---------------- DOM 引用 ---------------- */
const $ = (id) => document.getElementById(id);
const btnCamera = $("btn-camera");
const btnUpload = $("btn-upload");
const fileInput = $("file-input");
const cameraPanel = $("camera-panel");
const videoEl = $("video");
const cameraTip = $("camera-tip");
const previewPanel = $("preview-panel");
const previewImg = $("preview-img");
const resultPanel = $("result-panel");
const statusEl = $("status");
const errorEl = $("error");

let stream = null; // 摄像头媒体流
let currentBlob = null; // 待识别的图片 blob（已压缩为 JPEG）

/* ---------------- 相机相关 ---------------- */
async function openCamera() {
  hideError();
  hideStatus();
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    showError("当前浏览器不支持摄像头调用，请改用「上传图片」。");
    return;
  }
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } },
      audio: false,
    });
    videoEl.srcObject = stream;
    cameraPanel.hidden = false;
    resultPanel.hidden = true;
    previewPanel.hidden = true;
    cameraTip.textContent = "摄像头开启中…";
  } catch (err) {
    // 常见原因：非 HTTPS/localhost 环境
    showError(
      "无法打开摄像头（可能因为非 HTTPS 环境）：" + err.message +
      "\n\n请改用「上传图片」，手机端同样可以选择相机拍摄。"
    );
  }
}

function closeCamera() {
  if (stream) {
    stream.getTracks().forEach((t) => t.stop());
    stream = null;
  }
  videoEl.srcObject = null;
  cameraPanel.hidden = true;
}

function capturePhoto() {
  if (!stream) return;
  const w = videoEl.videoWidth;
  const h = videoEl.videoHeight;
  if (!w || !h) {
    cameraTip.textContent = "视频尚未就绪，请稍候再试";
    return;
  }
  const canvas = document.createElement("canvas");
  const maxDim = 1600;
  const scale = Math.min(1, maxDim / Math.max(w, h));
  canvas.width = Math.round(w * scale);
  canvas.height = Math.round(h * scale);
  const ctx = canvas.getContext("2d");
  ctx.drawImage(videoEl, 0, 0, canvas.width, canvas.height);

  canvas.toBlob((blob) => {
    if (!blob) {
      showError("拍照失败，请重试");
      return;
    }
    currentBlob = blob;
    showPreview(URL.createObjectURL(blob));
    closeCamera();
  }, "image/jpeg", 0.85);
}

async function switchCamera() {
  if (!stream) return;
  const track = stream.getVideoTracks()[0];
  if (!track || typeof track.getSettings !== "function") return;
  const current = track.getSettings().facingMode;
  const next = current === "environment" ? "user" : "environment";
  try {
    stream.getTracks().forEach((t) => t.stop());
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { exact: next }, width: { ideal: 1920 }, height: { ideal: 1080 } },
      audio: false,
    });
    videoEl.srcObject = stream;
  } catch (err) {
    showError("切换镜头失败：" + err.message);
  }
}

/* ---------------- 上传相关 ---------------- */
function onFileSelected() {
  const file = fileInput.files && fileInput.files[0];
  if (!file) return;
  hideError();
  hideStatus();

  if (!file.type.startsWith("image/")) {
    showError("请选择图片文件");
    return;
  }

  loadImageFile(file)
    .then((img) => {
      currentBlob = compressImage(img, 1600, 0.85);
      showPreview(URL.createObjectURL(currentBlob));
    })
    .catch((err) => showError("读取图片失败：" + err.message));
}

function loadImageFile(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("无法解析该图片"));
    img.src = url;
  });
}

/* 把 Image 压缩为 JPEG blob */
function compressImage(img, maxDim, quality) {
  const canvas = document.createElement("canvas");
  const w = img.naturalWidth || img.width;
  const h = img.naturalHeight || img.height;
  const scale = Math.min(1, maxDim / Math.max(w, h));
  canvas.width = Math.round(w * scale);
  canvas.height = Math.round(h * scale);
  canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
  // 同步取 blob（DataURL 兜底）
  let blob = null;
  canvas.toBlob((b) => (blob = b), "image/jpeg", quality);
  if (blob) return blob;
  // 兜底：DataURL 转 blob
  const dataUrl = canvas.toDataURL("image/jpeg", quality);
  const byteStr = atob(dataUrl.split(",")[1]);
  const arr = new Uint8Array(byteStr.length);
  for (let i = 0; i < byteStr.length; i++) arr[i] = byteStr.charCodeAt(i);
  return new Blob([arr], { type: "image/jpeg" });
}

/* ---------------- 预览 / 重置 ---------------- */
function showPreview(url) {
  previewImg.src = url;
  previewPanel.hidden = false;
  resultPanel.hidden = true;
  cameraPanel.hidden = true;
}

function resetAll() {
  closeCamera();
  currentBlob = null;
  previewPanel.hidden = true;
  resultPanel.hidden = true;
  hideStatus();
  hideError();
  fileInput.value = "";
}

/* ---------------- 识别 ---------------- */
async function analyze() {
  if (!currentBlob) return;
  hideError();
  resultPanel.hidden = true;
  showStatus("正在识别，请稍候…");

  const formData = new FormData();
  formData.append("file", currentBlob, "photo.jpg");

  try {
    const resp = await fetch("/api/analyze", { method: "POST", body: formData });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      throw new Error(data.detail || `请求失败（HTTP ${resp.status}）`);
    }
    renderResult(data);
  } catch (err) {
    showError(err.message);
  } finally {
    hideStatus();
  }
}

function renderResult(data) {
  const meta = RISK_META[data.risk_code] || RISK_META.unknown;
  $("result-badge").textContent = meta.icon;
  $("result-level").textContent = meta.label;
  $("result-level").style.color = meta.color;
  $("result-summary").textContent = data.summary || "";

  const reasonEl = $("result-reason");
  if (data.reason) {
    reasonEl.hidden = false;
    reasonEl.textContent = "判断依据：" + data.reason;
  } else {
    reasonEl.hidden = true;
  }

  resultPanel.style.background = meta.bg;
  resultPanel.hidden = false;
  resultPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/* ---------------- 提示条 ---------------- */
function showStatus(msg) {
  statusEl.textContent = msg;
  statusEl.hidden = false;
}
function hideStatus() {
  statusEl.hidden = true;
}
function showError(msg) {
  errorEl.textContent = msg;
  errorEl.hidden = false;
  errorEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
}
function hideError() {
  errorEl.hidden = true;
}

/* ---------------- 事件绑定 ---------------- */
btnCamera.addEventListener("click", openCamera);
btnUpload.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", onFileSelected);
$("btn-capture").addEventListener("click", capturePhoto);
$("btn-switch").addEventListener("click", switchCamera);
$("btn-close-camera").addEventListener("click", closeCamera);
$("btn-analyze").addEventListener("click", analyze);
$("btn-reset").addEventListener("click", resetAll);
