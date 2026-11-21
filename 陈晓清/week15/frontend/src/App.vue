<script setup>
import { computed, ref } from 'vue'

const message = ref('')
const loading = ref(false)
const result = ref(null)
const error = ref('')

const sentimentLabel = computed(() => (result.value?.sentiment === 'positive' ? '正向' : '负向'))

async function send() {
  const text = message.value.trim()
  if (!text || loading.value) return

  loading.value = true
  result.value = null
  error.value = ''

  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text }),
    })

    if (!response.ok) {
      let detail = '处理失败，请稍后重试'
      try {
        const data = await response.json()
        if (typeof data.detail === 'string') detail = data.detail
      } catch {
        // 响应不是 JSON 时使用默认提示
      }
      throw new Error(detail)
    }

    result.value = await response.json()
  } catch (err) {
    error.value = err.message || '网络错误，请检查后端是否启动'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="page">
    <section class="card">
      <header>
        <h1>情感支持对话</h1>
        <p class="subtitle">说说你的心情，系统会识别情感并给出回复。</p>
      </header>

      <form class="composer" @submit.prevent="send">
        <textarea
          v-model="message"
          rows="4"
          aria-label="输入内容"
          placeholder="今天工作好累，感觉什么都做不好……"
          :disabled="loading"
        ></textarea>
        <button type="submit" :disabled="loading || !message.trim()">
          {{ loading ? '处理中…' : '发送' }}
        </button>
      </form>

      <p v-if="error" class="error">{{ error }}</p>

      <section v-if="result" class="result">
        <div class="sentiment">
          <span class="label">情感识别</span>
          <span class="badge" :class="result.sentiment">{{ sentimentLabel }}</span>
        </div>
        <div class="reply">
          <span class="label">回复</span>
          <p>{{ result.reply }}</p>
        </div>
      </section>
    </section>
  </main>
</template>

<style>
* {
  box-sizing: border-box;
}

body {
  margin: 0;
  min-height: 100vh;
  font-family: system-ui, -apple-system, 'Segoe UI', 'Microsoft YaHei', sans-serif;
  background: linear-gradient(160deg, #eef4ff 0%, #f8fafc 45%, #fdf2f8 100%);
  color: #1f2937;
}
</style>

<style scoped>
.page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
}

.card {
  width: min(680px, 100%);
  padding: 32px;
  background: #fff;
  border-radius: 20px;
  box-shadow: 0 18px 50px rgba(30, 64, 175, 0.12);
}

h1 {
  margin: 0 0 8px;
  font-size: 26px;
}

.subtitle {
  margin: 0 0 24px;
  color: #6b7280;
  font-size: 14px;
}

.composer {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

textarea {
  width: 100%;
  padding: 14px;
  font: inherit;
  line-height: 1.6;
  resize: vertical;
  border: 1px solid #d1d5db;
  border-radius: 12px;
  outline: none;
  transition: border-color 0.2s, box-shadow 0.2s;
}

textarea:focus {
  border-color: #6366f1;
  box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.15);
}

textarea:disabled {
  background: #f9fafb;
}

button {
  align-self: flex-end;
  padding: 10px 28px;
  font: inherit;
  font-weight: 600;
  color: #fff;
  background: #4f46e5;
  border: 0;
  border-radius: 10px;
  cursor: pointer;
  transition: background 0.2s;
}

button:hover:not(:disabled) {
  background: #4338ca;
}

button:disabled {
  background: #c7d2fe;
  cursor: not-allowed;
}

.error {
  margin: 16px 0 0;
  color: #b91c1c;
  font-size: 14px;
}

.result {
  display: flex;
  flex-direction: column;
  gap: 16px;
  margin-top: 24px;
  padding-top: 24px;
  border-top: 1px solid #e5e7eb;
}

.sentiment {
  display: flex;
  align-items: center;
  gap: 10px;
}

.label {
  color: #6b7280;
  font-size: 13px;
}

.badge {
  padding: 3px 12px;
  border-radius: 999px;
  font-size: 13px;
  font-weight: 600;
}

.badge.positive {
  color: #047857;
  background: #d1fae5;
}

.badge.negative {
  color: #b45309;
  background: #fef3c7;
}

.reply p {
  margin: 8px 0 0;
  line-height: 1.8;
  white-space: pre-wrap;
}
</style>
