<template>
  <section class="chat-page">
    <!-- 左侧：会话列表（F3-7） -->
    <el-aside width="230px" class="conv-aside">
      <el-button type="primary" class="new-conv-btn" @click="newConversation">+ 新对话</el-button>
      <div class="conv-list">
        <div
          v-for="c in conversations" :key="c.id"
          class="conv-item" :class="{ active: c.id === currentConvId }"
          @click="loadConversation(c)"
        >
          <el-icon><ChatDotRound /></el-icon>
          <span class="conv-title">{{ c.title }}</span>
        </div>
        <div v-if="!conversations.length" class="conv-empty">暂无历史会话</div>
      </div>
    </el-aside>

    <div class="chat-main">
      <!-- 消息区 -->
      <div ref="msgBox" class="msg-box">
        <div v-if="!messages.length" class="chat-empty">
          <h2>多模态 RAG 问答</h2>
          <p>请先在「文档管理」页上传并解析 PDF；提问支持附带图片（图问）。</p>
        </div>
        <div v-for="(m, i) in messages" :key="i" class="msg-row" :class="m.role">
          <div class="bubble" :class="m.role">
            <!-- 用户消息：文本 + 缩略图 -->
            <template v-if="m.role === 'user'">
              <div>{{ m.text }}</div>
              <div v-if="m.images?.length" class="msg-images">
                <el-image
                  v-for="(u, j) in m.images" :key="j"
                  :src="u" :preview-src-list="m.images" fit="cover"
                  class="msg-thumb"
                />
              </div>
            </template>
            <!-- 助手消息：Markdown 图文混排（流式） -->
            <template v-else>
              <MarkdownRenderer v-if="m.text" :markdown="m.text" />
              <div v-else class="thinking">
                <el-icon class="is-loading"><Loading /></el-icon>
                {{ m.status || '思考中...' }}
              </div>
              <!-- 来源引用（F3-6） -->
              <div v-if="m.citations?.length" class="citations">
                <span class="cite-label">来源：</span>
                <el-tag
                  v-for="(c, j) in m.citations" :key="j"
                  size="small" :type="c.chunk_type === 'IMAGE' ? 'warning' : 'info'"
                  class="cite-tag"
                >
                  {{ c.chunk_type === 'IMAGE' ? '🖼' : '📄' }} {{ c.file_name }}{{ c.page_no != null ? ` 第${c.page_no}页` : '' }}
                </el-tag>
              </div>
            </template>
          </div>
        </div>
      </div>

      <!-- 输入区（F3-1：文本 + 图问；F3-2：检索范围） -->
      <div class="input-area">
        <div class="scope-bar">
          <el-select
            v-model="selectedDocIds" multiple collapse-tags collapse-tags-tooltip
            placeholder="检索范围：全部文档" clearable size="small" class="scope-select"
          >
            <el-option
              v-for="d in docOptions" :key="d.id"
              :label="d.file_name" :value="d.id"
            />
          </el-select>
          <el-upload
            v-model:file-list="imgFiles"
            accept="image/jpeg,image/png,image/webp"
            list-type="picture-card" auto-upload="false" :limit="3"
            class="img-upload" :on-change="onImgChange"
          >
            <el-icon><Plus /></el-icon>
            <template #tip>
              <span class="img-tip">附图（图问，≤3 张，单张 ≤10MB）</span>
            </template>
          </el-upload>
        </div>
        <div class="input-bar">
          <el-input
            v-model="input" type="textarea" :rows="2" resize="none"
            placeholder="输入问题，Enter 发送，Shift+Enter 换行"
            @keydown.enter.exact.prevent="handleSend"
          />
          <el-button
            v-if="!sending" type="primary" :disabled="!input.trim()"
            class="send-btn" @click="handleSend"
          >发送</el-button>
          <el-button v-else type="danger" plain class="send-btn" @click="handleStop">停止</el-button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
// 功能3（F3-1~F3-8）：多模态问答
// - 文本输入 + 附图（图问，转 base64 随请求发送）
// - 检索范围限定（全部 / 选中文档）
// - SSE 流式接收回答，Markdown 图文混排渲染，结束后展示来源引用
import { nextTick, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ChatDotRound, Loading, Plus } from '@element-plus/icons-vue'

import { chatStream, listConversations, listDocuments, listMessages } from '../api'
import MarkdownRenderer from '../components/MarkdownRenderer.vue'

const MAX_CHAT_IMAGES = 3
const MAX_IMG_MB = 10

// ---------- 会话列表 ----------
const conversations = ref([])
const currentConvId = ref(null)

async function refreshConversations() {
  try {
    conversations.value = await listConversations()
  } catch { /* 静默 */ }
}

function newConversation() {
  currentConvId.value = null
  messages.value = []
}

async function loadConversation(conv) {
  currentConvId.value = conv.id
  try {
    const rows = await listMessages(conv.id)
    messages.value = rows.map((m) => ({
      role: m.role === 'user' ? 'user' : 'assistant',
      text: m.text_content || '',
      images: m.image_urls || [],
      citations: m.citations || [],
    }))
    scrollToBottom()
  } catch (e) {
    ElMessage.error(`加载会话失败: ${e.message}`)
  }
}

// ---------- 消息与发送 ----------
const messages = ref([])
const input = ref('')
const sending = ref(false)
const msgBox = ref(null)
let abortController = null

function scrollToBottom() {
  nextTick(() => {
    if (msgBox.value) msgBox.value.scrollTop = msgBox.value.scrollHeight
  })
}

async function handleSend() {
  const query = input.value.trim()
  if (!query || sending.value) return

  // 附图 -> base64（图问）
  let images = []
  try {
    images = await Promise.all(imgFiles.value.map((f) => fileToBase64(f.raw ?? f)))
  } catch (e) {
    ElMessage.error(`图片读取失败: ${e.message}`)
    return
  }
  const imagePreviews = imgFiles.value.map((f) => URL.createObjectURL(f.raw ?? f))

  const userMsg = { role: 'user', text: query, images: imagePreviews }
  const assistantMsg = { role: 'assistant', text: '', status: '', citations: [] }
  messages.value.push(userMsg, assistantMsg)
  input.value = ''
  imgFiles.value = []
  sending.value = true
  scrollToBottom()

  abortController = new AbortController()
  const assistant = assistantMsg
  try {
    await chatStream(
      {
        query,
        images,
        document_ids: selectedDocIds.value,
        conversation_id: currentConvId.value,
      },
      {
        onMeta: (d) => { currentConvId.value = d.conversation_id },
        onStatus: (s) => { assistant.status = s },
        onChunk: (c) => { assistant.text += c; scrollToBottom() },
        onDone: (citations) => {
          assistant.citations = citations
          assistant.status = ''
        },
        onError: (msg) => {
          assistant.text += assistant.text ? '' : `**出错了：** ${msg}`
          assistant.status = ''
        },
      },
      abortController.signal,
    )
    refreshConversations()
  } catch (e) {
    if (e.name !== 'AbortError') {
      assistant.text += assistant.text ? '' : `**请求失败：** ${e.message}`
    }
  } finally {
    assistant.status = ''
    sending.value = false
    abortController = null
    scrollToBottom()
  }
}

function handleStop() {
  abortController?.abort()
}

// ---------- 附图（图问） ----------
const imgFiles = ref([])

function onImgChange(file) {
  if (file.raw && file.raw.size > MAX_IMG_MB * 1024 * 1024) {
    ElMessage.warning(`图片不能超过 ${MAX_IMG_MB}MB`)
    imgFiles.value = imgFiles.value.filter((f) => f.uid !== file.uid)
  }
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result.split(',')[1])
    reader.onerror = () => reject(new Error('读取失败'))
    reader.readAsDataURL(file)
  })
}

// ---------- 检索范围（F3-2） ----------
const docOptions = ref([])
const selectedDocIds = ref([])

onMounted(async () => {
  refreshConversations()
  try {
    const data = await listDocuments({ page: 1, page_size: 100 })
    docOptions.value = data.items
  } catch { /* 静默 */ }
})
</script>

<style scoped>
.chat-page { display: flex; gap: 16px; height: calc(100vh - 100px); max-width: 1280px; margin: 0 auto; }
.conv-aside { background: #fff; border-radius: 8px; padding: 12px; border: 1px solid #ebeef5; }
.new-conv-btn { width: 100%; }
.conv-list { margin-top: 12px; overflow-y: auto; }
.conv-item { display: flex; align-items: center; gap: 8px; padding: 9px 10px; border-radius: 6px; cursor: pointer; color: #303133; font-size: 13px; }
.conv-item:hover { background: #f5f7fa; }
.conv-item.active { background: #ecf5ff; color: #409eff; }
.conv-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.conv-empty { color: #c0c4cc; font-size: 12px; text-align: center; margin-top: 20px; }

.chat-main { flex: 1; display: flex; flex-direction: column; background: #fff; border-radius: 8px; border: 1px solid #ebeef5; min-width: 0; }
.msg-box { flex: 1; overflow-y: auto; padding: 20px 24px; }
.chat-empty { text-align: center; color: #909399; margin-top: 18vh; }
.chat-empty p { font-size: 13px; }

.msg-row { display: flex; margin-bottom: 14px; }
.msg-row.user { justify-content: flex-end; }
.msg-row.assistant { justify-content: flex-start; }
.bubble { max-width: 78%; padding: 10px 14px; border-radius: 10px; line-height: 1.6; font-size: 14px; }
.bubble.user { background: #409eff; color: #fff; white-space: pre-wrap; }
.bubble.assistant { background: #f5f7fa; color: #303133; }
.msg-images { display: flex; gap: 6px; margin-top: 8px; }
.msg-thumb { width: 88px; height: 88px; border-radius: 6px; }
.thinking { color: #909399; font-size: 13px; display: flex; align-items: center; gap: 6px; }

.citations { margin-top: 10px; padding-top: 8px; border-top: 1px dashed #dcdfe6; }
.cite-label { font-size: 12px; color: #909399; }
.cite-tag { margin: 2px 4px 2px 0; }

.input-area { border-top: 1px solid #ebeef5; padding: 12px 16px; }
.scope-bar { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 10px; }
.scope-select { width: 280px; }
.img-upload :deep(.el-upload--picture-card) { width: 56px; height: 56px; }
.img-upload :deep(.el-upload-list--picture-card .el-upload-list__item) { width: 56px; height: 56px; }
.img-tip { font-size: 12px; color: #909399; }
.input-bar { display: flex; gap: 10px; align-items: flex-end; }
.send-btn { height: 54px; width: 84px; }
</style>
