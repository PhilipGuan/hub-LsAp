const camera = document.querySelector("#camera");
const upload = document.querySelector("#upload");
const analyze = document.querySelector("#analyze");
const clear = document.querySelector("#clear");
const preview = document.querySelector("#preview");
const previewArea = document.querySelector("#preview-area");
const status = document.querySelector("#status");
const result = document.querySelector("#result");
const riskLevel = document.querySelector("#risk-level");
const reason = document.querySelector("#reason");
const levels = { "高风险": "high", "中风险": "medium", "低风险": "low", "无风险": "safe", "非楼道": "other" };
let selectedFile = null;
let previewUrl = null;
let busy = false;

function reset() {
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  selectedFile = null;
  previewUrl = null;
  preview.removeAttribute("src");
  previewArea.hidden = true;
  result.hidden = true;
  riskLevel.textContent = "";
  reason.textContent = "";
  status.textContent = "";
  status.className = "";
  document.querySelector("#file-name").textContent = "";
  camera.value = "";
  upload.value = "";
  analyze.disabled = true;
  clear.disabled = true;
}

function showError(message) {
  status.textContent = message;
  status.className = "error";
}

function selectFile(event) {
  const file = event.target.files[0];
  if (!file || busy) return;
  reset();
  const validType = ["image/jpeg", "image/png", "image/webp"].includes(file.type);
  const validExtension = /\.(jpe?g|png|webp)$/i.test(file.name);
  if (!validType || !validExtension) {
    showError("仅支持 JPEG、PNG、WebP 图片，不支持 HEIC。");
    return;
  }
  if (!file.size || file.size > 10 * 1024 * 1024) {
    showError(file.size ? "图片不能超过 10 MB。" : "图片为空，请重新选择。");
    return;
  }
  selectedFile = file;
  previewUrl = URL.createObjectURL(file);
  preview.src = previewUrl;
  previewArea.hidden = false;
  document.querySelector("#file-name").textContent = file.name;
  // 解码成功后才允许提交，避免将预览失败的图片送到模型。
  clear.disabled = false;
}

preview.addEventListener("load", () => { if (selectedFile && !busy) analyze.disabled = false; });
preview.addEventListener("error", () => {
  if (!selectedFile) return;
  reset();
  showError("图片无法预览，请重新选择 JPEG、PNG 或 WebP 图片。");
});
camera.addEventListener("change", selectFile);
upload.addEventListener("change", selectFile);
clear.addEventListener("click", reset);

analyze.addEventListener("click", async () => {
  if (!selectedFile || busy) return;
  busy = true;
  result.hidden = true;
  riskLevel.textContent = "";
  reason.textContent = "";
  status.className = "";
  status.textContent = "正在分析，请稍候…";
  analyze.textContent = "分析中…";
  for (const element of [camera, upload, analyze, clear]) element.disabled = true;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 300_000);
  try {
    const form = new FormData();
    form.append("file", selectedFile);
    // 前端请求后端的总等待时间为 5 分钟，超时后退出等待并允许重试。
    const response = await fetch("/api/analyze", {
      method: "POST", body: form, cache: "no-store", signal: controller.signal,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || "分析失败，请重新拍摄或重试");
    if (!Object.hasOwn(levels, data.risk_level) || typeof data.reason !== "string" || !data.reason.trim()) {
      throw new Error("分析失败，请重新拍摄或重试");
    }
    riskLevel.textContent = data.risk_level;
    riskLevel.className = `badge ${levels[data.risk_level]}`;
    reason.textContent = data.reason;
    result.hidden = false;
    status.textContent = "分析完成";
  } catch (error) {
    const message = error instanceof TypeError || error instanceof SyntaxError || error.name === "AbortError"
      ? "分析失败，请重新拍摄或重试" : error.message;
    showError(message);
  } finally {
    clearTimeout(timer);
    busy = false;
    for (const element of [camera, upload, analyze, clear]) element.disabled = false;
    analyze.textContent = "开始分析";
  }
});

// 后退缓存恢复页面时也清空，不恢复旧照片和旧分析结果。
window.addEventListener("pageshow", (event) => { if (event.persisted) location.reload(); });
reset();
