const cameraButton = document.getElementById("camera-button");
const chooseButton = document.getElementById("choose-button");
const cameraFallback = document.getElementById("camera-fallback");
const imagePicker = document.getElementById("image-picker");
const photoPreview = document.getElementById("photo-preview");
const emptyState = document.getElementById("empty-state");
const photoMeta = document.getElementById("photo-meta");
const photoName = document.getElementById("photo-name");
const cameraPanel = document.getElementById("camera-panel");
const cameraVideo = document.getElementById("camera-video");
const cameraControls = document.getElementById("camera-controls");
const captureButton = document.getElementById("capture-button");
const cancelCamera = document.getElementById("cancel-camera");
const analyzeButton = document.getElementById("analyze-button");
const analyzeLabel = document.getElementById("analyze-label");
const analyzeSpinner = document.getElementById("analyze-spinner");
const errorMessage = document.getElementById("error-message");
const resultCard = document.getElementById("result-card");
const resultTitle = document.getElementById("result-title");
const riskBadge = document.getElementById("risk-badge");
const evidenceList = document.getElementById("evidence-list");
const recommendationText = document.getElementById("recommendation-text");
const resultEvidenceWrap = document.getElementById("result-evidence-wrap");
const recommendationWrap = document.getElementById("recommendation-wrap");
const captureCanvas = document.getElementById("capture-canvas");
const removePhotoButton = document.getElementById("remove-photo");

const riskLabels = {
  none: { label: "无隐患", className: "risk-none" },
  low: { label: "低风险", className: "risk-low" },
  medium: { label: "中等风险", className: "risk-medium" },
  high: { label: "高风险", className: "risk-high" },
};

let selectedFile = null;
let previewUrl = null;
let cameraStream = null;
let isAnalyzing = false;

cameraButton.addEventListener("click", openCamera);
chooseButton.addEventListener("click", () => imagePicker.click());
imagePicker.addEventListener("change", (event) => acceptInputFile(event, "请选择一张照片。"));
cameraFallback.addEventListener("change", (event) => acceptInputFile(event, "拍照后没有取得照片，请重试。"));
captureButton.addEventListener("click", captureFrame);
cancelCamera.addEventListener("click", stopCamera);
analyzeButton.addEventListener("click", analyzePhoto);
removePhotoButton.addEventListener("click", clearPhoto);
window.addEventListener("pagehide", stopCamera);

async function openCamera() {
  clearError();
  hideResult();

  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    cameraFallback.click();
    return;
  }

  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" } },
      audio: false,
    });
    cameraVideo.srcObject = cameraStream;
    emptyState.hidden = true;
    photoPreview.hidden = true;
    photoMeta.hidden = true;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = null;
    photoPreview.removeAttribute("src");
    cameraPanel.hidden = false;
    cameraControls.hidden = false;
    sourceActionsHidden(true);
    analyzeButton.hidden = true;
    selectedFile = null;
    analyzeButton.disabled = true;
  } catch (error) {
    if (error.name === "NotAllowedError" || error.name === "PermissionDeniedError") {
      showError("浏览器未授权摄像头。你可以允许摄像头访问，或改用“选择本地图片”。");
    } else if (error.name === "NotFoundError" || error.name === "DevicesNotFoundError") {
      showError("没有找到可用摄像头，请选择本地图片。");
    } else {
      showError("无法打开摄像头，请检查浏览器权限，或选择本地图片。");
    }
    cameraFallback.click();
  }
}

function sourceActionsHidden(hidden) {
  document.querySelector(".source-actions").hidden = hidden;
}

function captureFrame() {
  const width = cameraVideo.videoWidth;
  const height = cameraVideo.videoHeight;
  if (!width || !height) {
    showError("摄像头画面尚未就绪，请稍等后再拍摄。");
    return;
  }

  captureCanvas.width = width;
  captureCanvas.height = height;
  const context = captureCanvas.getContext("2d");
  context.drawImage(cameraVideo, 0, 0, width, height);
  captureCanvas.toBlob((blob) => {
    if (!blob) {
      showError("生成照片失败，请重试。");
      return;
    }
    acceptFile(new File([blob], `hallway-${Date.now()}.jpg`, { type: "image/jpeg" }));
  }, "image/jpeg", 0.92);
}

function acceptInputFile(event, emptyMessage) {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file) {
    if (emptyMessage) showError(emptyMessage);
    return;
  }
  acceptFile(file);
}

function acceptFile(file) {
  clearError();
  hideResult();
  if (!file.type.startsWith("image/")) {
    showError("所选文件不是图片，请选择 JPEG、PNG、GIF、WebP 或 BMP 图片。");
    return;
  }
  if (file.size > 15 * 1024 * 1024) {
    showError("图片不能超过 15 MB，请压缩后重试。");
    return;
  }

  stopCamera();
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  selectedFile = file;
  previewUrl = URL.createObjectURL(file);
  photoPreview.src = previewUrl;
  photoPreview.hidden = false;
  emptyState.hidden = true;
  cameraPanel.hidden = true;
  cameraControls.hidden = true;
  photoMeta.hidden = false;
  photoName.textContent = file.name || "拍摄照片";
  sourceActionsHidden(false);
  analyzeButton.hidden = false;
  analyzeButton.disabled = false;
  analyzeLabel.textContent = "开始风险识别";
}

async function analyzePhoto() {
  if (!selectedFile || isAnalyzing) return;
  isAnalyzing = true;
  clearError();
  hideResult();
  analyzeButton.disabled = true;
  cameraButton.disabled = true;
  chooseButton.disabled = true;
  removePhotoButton.disabled = true;
  analyzeSpinner.hidden = false;
  analyzeLabel.textContent = "正在分析照片…";

  const formData = new FormData();
  formData.append("file", selectedFile, selectedFile.name || "hallway-photo.jpg");

  try {
    const response = await fetch("/api/analyze", { method: "POST", body: formData });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "识别失败，请重试。");
    renderResult(data);
  } catch (error) {
    showError(error.message || "连接服务失败，请确认后端正在运行后重试。");
  } finally {
    isAnalyzing = false;
    analyzeButton.disabled = !selectedFile;
    cameraButton.disabled = false;
    chooseButton.disabled = false;
    removePhotoButton.disabled = false;
    analyzeSpinner.hidden = true;
    analyzeLabel.textContent = "重新识别";
  }
}

function renderResult(result) {
  evidenceList.replaceChildren();
  if (result.scene === "non_hallway") {
    resultTitle.textContent = "这张照片不是楼道场景";
    riskBadge.textContent = "非楼道";
    riskBadge.className = "risk-badge risk-non-hallway";
    recommendationWrap.hidden = !result.recommendation;
  } else {
    const risk = riskLabels[result.risk_level];
    if (!risk) {
      showError("服务返回了无法识别的风险等级，请重新识别。");
      return;
    }
    resultTitle.textContent = "楼道风险等级";
    riskBadge.textContent = risk.label;
    riskBadge.className = `risk-badge ${risk.className}`;
    recommendationWrap.hidden = !result.recommendation;
  }
  resultEvidenceWrap.hidden = !(result.evidence || []).length;
  (result.evidence || []).forEach((item) => {
    const row = document.createElement("li");
    row.textContent = item;
    evidenceList.append(row);
  });
  recommendationText.textContent = result.recommendation || "";
  resultCard.hidden = false;
  resultCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function clearPhoto() {
  if (isAnalyzing) return;
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = null;
  selectedFile = null;
  photoPreview.removeAttribute("src");
  photoPreview.hidden = true;
  photoMeta.hidden = true;
  emptyState.hidden = false;
  resultCard.hidden = true;
  analyzeButton.disabled = true;
  analyzeLabel.textContent = "开始风险识别";
  clearError();
}

function hideResult() {
  resultCard.hidden = true;
  resultEvidenceWrap.hidden = false;
  recommendationWrap.hidden = false;
}

function showError(message) {
  errorMessage.textContent = message;
  errorMessage.hidden = false;
}

function clearError() {
  errorMessage.textContent = "";
  errorMessage.hidden = true;
}

function stopCamera() {
  if (cameraStream) {
    cameraStream.getTracks().forEach((track) => track.stop());
    cameraStream = null;
  }
  cameraVideo.srcObject = null;
  cameraPanel.hidden = true;
  cameraControls.hidden = true;
  sourceActionsHidden(false);
  analyzeButton.hidden = false;
}
