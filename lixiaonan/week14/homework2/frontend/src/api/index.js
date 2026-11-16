// 后端接口封装（对应《需求文档》§5）
// 统一约定：后端返回 {code, message, data}，code=0 视为成功，否则抛 Error(message)
import axios from 'axios'

const http = axios.create({ baseURL: '/api', timeout: 60000 })

async function unwrap(promise) {
  const resp = await promise
  const body = resp.data
  if (body.code !== 0) {
    throw new Error(body.message || `请求失败 (code=${body.code})`)
  }
  return body.data
}

// 功能1（F1-1~F1-5）：批量上传 PDF，返回逐文件结果 [{file_name, document_id, status, code, message}]
export function uploadDocuments(files) {
  const form = new FormData()
  for (const f of files) form.append('files', f, f.name)
  return unwrap(http.post('/v1/documents/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  }))
}

// 功能2（F2-1~F2-2）：文档列表（含解析状态），前端轮询调用
export function listDocuments(params = {}) {
  return unwrap(http.get('/v1/documents', { params }))
}

// 功能2（F2-3）：文档详情
export function getDocument(id) {
  return unwrap(http.get(`/v1/documents/${id}`))
}

// 功能2（F2-5）：解析结果 Markdown（图片引用已是可访问 URL）
export function getDocumentMarkdown(id) {
  return unwrap(http.get(`/v1/documents/${id}/markdown`))
}

// 功能2（F2-5）：失败任务重新解析
export function reparseDocument(id) {
  return unwrap(http.post(`/v1/documents/${id}/reparse`))
}

// 功能3（F3-8）：多模态问答 SSE 流式。事件：meta / status / chunk / done / error
export async function chatStream(payload, { onMeta, onStatus, onChunk, onDone, onError, signal }) {
  const resp = await fetch('/api/v1/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  })
  if (!resp.ok || !resp.body) {
    throw new Error(`问答请求失败 (HTTP ${resp.status})`)
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const events = buffer.split('\n\n')
    buffer = events.pop() || ''
    for (const ev of events) {
      const line = ev.trim()
      if (!line.startsWith('data:')) continue
      let data
      try {
        data = JSON.parse(line.slice(5).trim())
      } catch {
        continue
      }
      if (data.type === 'meta') onMeta?.(data)
      else if (data.type === 'status') onStatus?.(data.content)
      else if (data.type === 'chunk') onChunk?.(data.content)
      else if (data.type === 'done') onDone?.(data.citations || [])
      else if (data.type === 'error') onError?.(data.message)
    }
  }
}

// 功能3（F3-7）：会话列表与历史消息
export function listConversations() {
  return unwrap(http.get('/v1/conversations'))
}

export function listMessages(conversationId) {
  return unwrap(http.get(`/v1/conversations/${conversationId}/messages`))
}

export default http
