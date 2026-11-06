<template>
  <div class="app">
    <header class="header">
      <div class="brand">
        <div class="logo" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
            <ellipse cx="12" cy="5" rx="8" ry="3" />
            <path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5" />
            <path d="M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6" />
          </svg>
        </div>
        <div class="titles">
          <h1>Chinook SQL Agent</h1>
          <p>用自然语言查询音乐商店数据库</p>
        </div>
      </div>
      <div class="badge">
        <span class="dot"></span>
        LangGraph · 多 Agent
      </div>
    </header>

    <main ref="scrollEl" class="chat">
      <section v-if="!messages.length" class="welcome">
        <h2>用一句话，查询 Chinook 数据库</h2>
        <p class="lead">
          Agent 会依次完成选表、生成 SQL、校验、执行与结果汇总，
          并把这中间的 SQL 和执行结果展示给你。
        </p>
        <div class="examples">
          <button
            v-for="ex in examples"
            :key="ex.text"
            class="example"
            :disabled="loading"
            @click="send(ex.text)"
          >
            <span class="example-icon">{{ ex.icon }}</span>
            <span>{{ ex.text }}</span>
          </button>
        </div>
      </section>

      <div
        v-for="(message, index) in messages"
        :key="index"
        class="message"
        :class="[message.role, { error: message.error }]"
      >
        <div class="avatar" aria-hidden="true">
          {{ message.role === 'user' ? '你' : 'AI' }}
        </div>
        <div class="bubble">
          <p class="text">{{ message.content }}</p>

          <div v-if="message.detail" class="detail">
            <button class="toggle" @click="message.open = !message.open">
              <span>{{ message.open ? '收起执行过程' : '查看执行过程' }}</span>
              <svg
                class="chevron"
                :class="{ open: message.open }"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
              >
                <path d="m6 9 6 6 6-6" />
              </svg>
            </button>

            <div v-if="message.open" class="detail-body">
              <div class="meta">
                <span class="tag">数据表：{{ message.detail.table || '—' }}</span>
                <span class="tag">耗时：{{ message.detail.elapsed_ms }} ms</span>
                <span class="tag">{{ message.detail.rows.length }} 行</span>
              </div>

              <div class="sql">
                <div class="sql-head">SQL</div>
                <pre><code>{{ message.detail.sql || '—' }}</code></pre>
              </div>

              <div v-if="message.detail.columns.length" class="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th v-for="column in message.detail.columns" :key="column">{{ column }}</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(row, rowIndex) in message.detail.rows" :key="rowIndex">
                      <td v-for="(cell, cellIndex) in row" :key="cellIndex">{{ cell }}</td>
                    </tr>
                  </tbody>
                </table>
                <p v-if="!message.detail.rows.length" class="empty">查询结果为空</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div v-if="loading" class="message assistant">
        <div class="avatar" aria-hidden="true">AI</div>
        <div class="bubble typing">
          <span></span><span></span><span></span>
        </div>
      </div>
    </main>

    <footer class="composer">
      <div class="composer-inner">
        <textarea
          ref="textareaEl"
          v-model="input"
          rows="1"
          placeholder="例如：哪个国家的客户最多？"
          aria-label="输入问题"
          @keydown="onKeydown"
          @input="autoGrow"
        ></textarea>
        <button class="send" :disabled="!canSend" aria-label="发送" @click="send()">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M22 2 11 13" />
            <path d="M22 2 15 22l-4-9-9-4 20-7Z" />
          </svg>
        </button>
      </div>
      <p class="hint">Enter 发送 · Shift + Enter 换行 · 数据来源 ref/chinook.db</p>
    </footer>
  </div>
</template>

<script setup>
import { computed, nextTick, ref } from 'vue'

const examples = [
  { icon: '🌍', text: '哪个国家的客户最多？' },
  { icon: '💿', text: '专辑数量最多的前 5 位艺术家是谁？' },
  { icon: '🎵', text: 'Rock 风格的歌曲一共有多少首？' },
  { icon: '💰', text: '总销售额最高的员工是谁？' },
]

const messages = ref([])
const input = ref('')
const loading = ref(false)
const scrollEl = ref(null)
const textareaEl = ref(null)

const canSend = computed(() => input.value.trim().length > 0 && !loading.value)

function scrollToBottom() {
  nextTick(() => {
    const element = scrollEl.value
    if (element) element.scrollTop = element.scrollHeight
  })
}

function autoGrow() {
  const element = textareaEl.value
  if (!element) return
  element.style.height = 'auto'
  element.style.height = `${Math.min(element.scrollHeight, 160)}px`
}

async function send(preset) {
  const question = (preset ?? input.value).trim()
  if (!question || loading.value) return

  messages.value.push({ role: 'user', content: question })
  input.value = ''
  nextTick(autoGrow)
  loading.value = true
  scrollToBottom()

  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: question }),
    })
    const data = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(data.detail || `请求失败（HTTP ${response.status}）`)
    }

    messages.value.push({
      role: 'assistant',
      content: data.answer || '（没有返回内容）',
      open: false,
      detail: {
        table: data.table,
        sql: data.sql,
        columns: data.columns || [],
        rows: data.rows || [],
        elapsed_ms: data.elapsed_ms || 0,
      },
    })
  } catch (error) {
    messages.value.push({
      role: 'assistant',
      error: true,
      content: error?.message || '请求失败，请稍后重试。',
    })
  } finally {
    loading.value = false
    scrollToBottom()
  }
}

function onKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    send()
  }
}
</script>
