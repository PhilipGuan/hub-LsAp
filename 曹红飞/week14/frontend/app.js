// 前端逻辑：上传、状态轮询、多模态问答
const fileInput = document.getElementById("file-input");
const uploadBtn = document.getElementById("upload-btn");
const uploadMsg = document.getElementById("upload-msg");
const docsBody = document.querySelector("#docs-table tbody");
const questionEl = document.getElementById("question");
const qaImage = document.getElementById("qa-image");
const qaImgName = document.getElementById("qa-img-name");
const askBtn = document.getElementById("ask-btn");
const answerEl = document.getElementById("answer");

const STATUS_LABELS = {
  pending: "待解析",
  parsing: "解析中",
  done: "解析完成",
  failed: "解析失败",
};

// ---------- 上传 ----------
uploadBtn.addEventListener("click", async () => {
  const files = fileInput.files;
  if (!files.length) {
    uploadMsg.textContent = "请先选择 PDF 文件";
    return;
  }
  const form = new FormData();
  for (const f of files) form.append("files", f);

  uploadBtn.disabled = true;
  uploadMsg.textContent = "上传中…";
  try {
    const res = await fetch("/api/upload", { method: "POST", body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "上传失败");
    const noKafka = data.uploaded.filter((d) => !d.kafka_sent);
    uploadMsg.textContent = `已上传 ${data.uploaded.length} 个文件`;
    if (noKafka.length) {
      uploadMsg.textContent += "（Kafka 不可用，文件未进入解析队列，请检查 Kafka）";
    }
    fileInput.value = "";
    loadDocs();
  } catch (e) {
    uploadMsg.textContent = "上传失败: " + e.message;
  } finally {
    uploadBtn.disabled = false;
  }
});

// ---------- 状态轮询 ----------
async function loadDocs() {
  try {
    const res = await fetch("/api/docs");
    const data = await res.json();
    docsBody.innerHTML = "";
    if (!data.docs.length) {
      docsBody.innerHTML = '<tr><td colspan="3" class="hint">暂无文档</td></tr>';
      return;
    }
    for (const d of data.docs) {
      const tr = document.createElement("tr");
      const errorHint = d.error
        ? `<span class="hint" title="${escapeHtml(d.error)}">（查看原因）</span>`
        : "";
      tr.innerHTML = `
        <td>${escapeHtml(d.filename)}</td>
        <td><span class="badge ${escapeHtml(d.status)}">${escapeHtml(d.status_label || d.status)}</span>${errorHint}</td>
        <td>${escapeHtml(d.created_at)}</td>`;
      docsBody.appendChild(tr);
    }
  } catch (e) {
    console.error("获取文档列表失败", e);
  }
}

// ---------- 问答 ----------
qaImage.addEventListener("change", () => {
  qaImgName.textContent = qaImage.files.length ? qaImage.files[0].name : "";
});

askBtn.addEventListener("click", async () => {
  const question = questionEl.value.trim();
  if (!question && !qaImage.files.length) {
    answerEl.textContent = "请输入问题或选择图片";
    return;
  }
  const form = new FormData();
  form.append("question", question);
  if (qaImage.files.length) form.append("image", qaImage.files[0]);

  askBtn.disabled = true;
  answerEl.innerHTML = '<div class="loading">思考中…</div>';
  try {
    const res = await fetch("/api/ask", { method: "POST", body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "问答失败");
    renderAnswer(data);
  } catch (e) {
    answerEl.textContent = "问答失败: " + e.message;
  } finally {
    askBtn.disabled = false;
  }
});

function renderAnswer(data) {
  let html = escapeHtml(data.answer);
  for (const img of data.images) {
    const tag = `<figure><img src="${img.url}" alt="${escapeHtml(img.caption)}">` +
      `<figcaption>${escapeHtml(img.caption)}</figcaption></figure>`;
    if (html.includes(img.caption)) {
      // 用图片替换正文中的“图N”引用，实现图文混排
      html = html.split(img.caption).join(tag);
    } else {
      // 未被引用则在末尾展示
      html += tag;
    }
  }
  answerEl.innerHTML = html;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// 页面加载：先拉一次，之后每 3 秒轮询
loadDocs();
setInterval(loadDocs, 3000);
