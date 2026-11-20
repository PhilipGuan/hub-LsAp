const byId = (id) => document.getElementById(id);
const uploadForm = byId('upload-form');
const askForm = byId('ask-form');
const pdfs = byId('pdfs');
const question = byId('question');
const image = byId('image');
const dropZone = byId('drop-zone');
const uploadButton = byId('upload-button');
const askButton = byId('ask-button');
const messages = byId('messages');
const statusLabels = { queued: '待解析', parsing: '解析中', complete: '解析完成', failed: '解析失败' };
let lastDocuments;
let refreshTimer;
let refreshId = 0;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function api(path, options) {
  const response = await fetch(`/api/${path}`, options);
  const data = await response.json();
  if (!response.ok && !data.errors) throw new Error(data.error || '请求失败。');
  return data;
}

function renderDocuments(documents) {
  const list = byId('document-list');
  if (!documents.length) {
    list.replaceChildren(element('p', 'empty-docs', '还没有上传文档。'));
    return;
  }
  list.replaceChildren(...documents.map((doc) => {
    const row = element('article', 'document');
    const name = element('span', 'document-name', doc.name);
    name.title = doc.name;
    row.append(name, element('span', `status ${doc.status}`, statusLabels[doc.status] || doc.status));
    if (doc.error) row.append(element('p', 'document-error', doc.error));
    return row;
  }));
}

async function refreshDocuments() {
  clearTimeout(refreshTimer);
  const id = ++refreshId;
  let interval = 5000;
  try {
    const docs = await api('documents');
    if (id !== refreshId) return;
    const snapshot = JSON.stringify(docs);
    if (snapshot !== lastDocuments) renderDocuments(docs);
    lastDocuments = snapshot;
    if (docs.some(({ status }) => status === 'queued' || status === 'parsing')) interval = 1500;
  } catch (error) {
    if (id !== refreshId) return;
    const snapshot = error.message;
    if (snapshot !== lastDocuments) byId('document-list').replaceChildren(element('p', 'feedback', snapshot));
    lastDocuments = snapshot;
  } finally {
    if (id === refreshId) refreshTimer = setTimeout(refreshDocuments, interval);
  }
}

function updateSelection() {
  const count = pdfs.files.length;
  byId('selection').textContent = count ? `已选择 ${count} 份 PDF` : '选择或拖入 PDF';
  uploadButton.disabled = !count;
}

pdfs.addEventListener('change', updateSelection);
dropZone.addEventListener('dragover', (event) => { event.preventDefault(); dropZone.classList.add('dragging'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragging'));
dropZone.addEventListener('drop', (event) => {
  event.preventDefault();
  dropZone.classList.remove('dragging');
  if (pdfs.disabled) return;
  pdfs.files = event.dataTransfer.files;
  updateSelection();
});

uploadForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const feedback = byId('upload-feedback');
  const count = pdfs.files.length;
  if (!count || count > 10) {
    feedback.textContent = '每次请选择 1 至 10 份 PDF。';
    return;
  }
  const body = new FormData(uploadForm);
  pdfs.disabled = uploadButton.disabled = true;
  feedback.textContent = '上传中…';
  try {
    const result = await api('documents', { method: 'POST', body });
    feedback.textContent = [
      result.documents.length ? `${result.documents.length} 份文档已入队。` : '',
      ...result.errors.map(({ name, error }) => `${name}：${error}`),
    ].filter(Boolean).join('\n');
    if (result.documents.length) pdfs.value = '';
    await refreshDocuments();
  } catch (error) {
    feedback.textContent = error.message;
  } finally {
    pdfs.disabled = false;
    updateSelection();
  }
});

function updateAttachment() {
  const label = byId('attachment-name');
  const attachment = image.files[0];
  label.textContent = attachment?.name || '';
  label.hidden = byId('clear-image').hidden = !attachment;
}

image.addEventListener('change', updateAttachment);
byId('clear-image').addEventListener('click', () => { image.value = ''; updateAttachment(); });

function appendMessage(role) {
  byId('chat-empty')?.remove();
  const node = element('article', `message ${role}`);
  node.append(element('h2', 'speaker', role === 'user' ? '你' : '知识库'));
  messages.append(node);
  return node;
}

function addBlocks(node, blocks) {
  for (const block of blocks) {
    const section = element('section', 'answer-block');
    section.append(element('p', 'message-text', block.text));
    if (block.figure) {
      const figure = element('figure', 'answer-figure');
      const photo = element('img');
      photo.src = block.figure.url;
      photo.alt = block.figure.caption;
      photo.loading = 'lazy';
      figure.append(photo, element('figcaption', '', `${block.figure.document} · 第 ${block.figure.page} 页`));
      section.append(figure);
    }
    if (block.sources?.length) {
      section.append(element('p', 'sources', block.sources.map(({ document, page }) => `${document} · 第 ${page} 页`).join(' / ')));
    }
    node.append(section);
  }
}

askForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (askButton.disabled || !question.value.trim()) return;
  const body = new FormData();
  body.set('question', question.value.trim());
  const attachment = image.files[0];
  if (attachment) body.set('image', attachment);
  const user = appendMessage('user');
  user.append(element('p', 'message-text', body.get('question')));
  if (attachment) user.append(element('p', 'sources', `附件 · ${attachment.name}`));
  const answer = appendMessage('assistant');
  const pending = element('p', 'feedback', '检索并生成回答中…');
  answer.append(pending);
  answer.scrollIntoView({ block: 'nearest' });
  question.value = '';
  image.value = '';
  updateAttachment();
  askButton.disabled = true;
  try {
    const result = await api('ask', { method: 'POST', body });
    pending.remove();
    addBlocks(answer, result.blocks);
  } catch (error) {
    pending.textContent = error.message;
  } finally {
    askButton.disabled = false;
    answer.scrollIntoView({ block: 'nearest' });
  }
});

refreshDocuments();
