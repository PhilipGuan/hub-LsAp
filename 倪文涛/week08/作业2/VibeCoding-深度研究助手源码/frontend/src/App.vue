<script setup>
import { nextTick, onBeforeUnmount, ref } from 'vue'

const topic = ref('')
const running = ref(false)
const started = ref(false)
const finished = ref(false)
const statusText = ref('准备中…')
const statusType = ref('')
const timerText = ref('')
const lines = ref([])
const reportHtml = ref('')
const reportMd = ref('')
const copied = ref(false)
const logBox = ref(null)

let es = null
let timerId = null
let startedAt = 0

function log(cls, text) {
  lines.value.push({ cls: cls || 'info', text })
  nextTick(() => {
    if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight
  })
}

function setStatus(text, cls = '') {
  statusText.value = text
  statusType.value = cls
}

function startTimer() {
  startedAt = Date.now()
  timerId = setInterval(() => {
    const sec = Math.floor((Date.now() - startedAt) / 1000)
    const mm = String(Math.floor(sec / 60)).padStart(2, '0')
    const ss = String(sec % 60).padStart(2, '0')
    timerText.value = `⏱ ${mm}:${ss}`
  }, 500)
}

function stopTimer() {
  if (timerId) clearInterval(timerId)
  timerId = null
}

function finish() {
  running.value = false
  finished.value = true
  stopTimer()
}

// SSE 事件 → 状态与过程日志(与后端事件类型一一对应)
function handle(ev) {
  switch (ev.type) {
    case 'status':
      setStatus(ev.message)
      log('info', `ℹ️ ${ev.message}`)
      break
    case 'plan':
      log('plan', `📋 规划出 ${ev.sub_questions.length} 个子问题:`)
      ev.sub_questions.forEach((q, i) => log('plan', `   ${i + 1}. ${q}`))
      break
    case 'round':
      log('divider', `—— 第 ${ev.round}/${ev.max} 轮 ——`)
      break
    case 'queries':
      log('query', `🔍 检索词:${ev.queries.join(' / ')}`)
      break
    case 'search_result':
      log('hit', `   ✓「${ev.query}」命中 ${ev.count} 条(新增 ${ev.new})`)
      break
    case 'read':
      log('read', `📄 [${ev.sid}] ${ev.title}${ev.fetched ? '' : '(仅摘要)'} → ${ev.note}`)
      break
    case 'fetch_fail':
      log('warn', `⚠️ [${ev.sid}] 抓取失败(${ev.reason}),已降级使用搜索摘要`)
      break
    case 'reflect':
      log('info', ev.sufficient ? '🧠 评估:信息已充分' : `🧠 评估:存在缺口——${ev.reason}`)
      break
    case 'converge':
      log('info', `✅ 信息充分,提前收敛:${ev.reason || ''}`)
      break
    case 'skip':
      log('warn', `⚠️ ${ev.detail}`)
      break
    case 'report': {
      reportHtml.value = ev.html
      reportMd.value = ev.markdown
      const st = ev.stats
      setStatus(`完成 · ${st.rounds} 轮 · 搜索 ${st.searches} 次 · 正文 ${st.pages_fetched} 页`, 'ok')
      log('divider', `📊 统计:${st.rounds} 轮 / 搜索 ${st.searches} 次 / 抓取正文 ${st.pages_fetched} 页 / 降级 ${st.fallbacks} 页 / 跳过 ${st.skipped} 项`)
      break
    }
    case 'error':
      setStatus('出错了', 'err')
      log('error', `❌ ${ev.message}`)
      break
    case 'done':
      finish()
      break
  }
}

function start() {
  const t = topic.value.trim()
  if (t.length < 2 || running.value) return
  if (es) es.close()
  running.value = true
  finished.value = false
  started.value = true
  lines.value = []
  reportHtml.value = ''
  reportMd.value = ''
  setStatus('准备中…')
  startTimer()
  log('info', `🎯 研究主题:${t}`)

  es = new EventSource(`/api/research?topic=${encodeURIComponent(t)}`)
  es.onmessage = e => {
    let ev
    try {
      ev = JSON.parse(e.data)
    } catch {
      return
    }
    handle(ev)
    if (ev.type === 'done') es.close()
  }
  es.onerror = () => {
    if (!finished.value) {
      setStatus('连接中断', 'err')
      log('error', '❌ 连接中断,请重试')
    }
    if (es) es.close()
    finish()
  }
}

function downloadReport() {
  if (!reportHtml.value) return
  const name = (topic.value.trim().slice(0, 20).replace(/[\\/:*?"<>|]/g, '') || '研究报告')
  const d = new Date()
  const pad = n => String(n).padStart(2, '0')
  const blob = new Blob([reportHtml.value], { type: 'text/html;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `研究报告-${name}-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}.html`
  a.click()
  URL.revokeObjectURL(a.href)
}

async function copyMd() {
  if (!reportMd.value) return
  try {
    await navigator.clipboard.writeText(reportMd.value)
    copied.value = true
    setTimeout(() => (copied.value = false), 1500)
  } catch {
    alert('复制失败,请手动选择报告内容')
  }
}

onBeforeUnmount(() => {
  if (es) es.close()
  stopTimer()
})
</script>

<template>
  <header>
    <div class="inner">
      <h1>🔬 深度研究助手</h1>
      <p>输入主题,自动:规划拆解 → 多轮检索 → 阅读抽取 → 补检判断 → 综合生成带引用的研究报告</p>
    </div>
  </header>

  <main>
    <section class="card">
      <div class="input-row">
        <input
          v-model="topic"
          placeholder="输入研究主题,如:2026 年中国新能源汽车出口格局"
          autocomplete="off"
          @keydown.enter="start"
        >
        <button :disabled="running || topic.trim().length < 2" @click="start">
          {{ running ? '研究中…' : finished ? '重新研究' : '开始研究' }}
        </button>
      </div>
    </section>

    <section v-if="started" class="card">
      <div class="progress-head">
        <span class="status" :class="statusType">{{ statusText }}</span>
        <span class="timer">{{ timerText }}</span>
      </div>
      <div ref="logBox" class="log">
        <div v-for="(l, i) in lines" :key="i" class="line" :class="l.cls">{{ l.text }}</div>
      </div>
    </section>

    <section v-if="reportHtml" class="card">
      <div class="report-head">
        <span class="title">📑 研究报告</span>
        <span>
          <button class="ghost" @click="copyMd">{{ copied ? '已复制 ✓' : '复制 Markdown' }}</button>
          <button class="ghost" @click="downloadReport">下载 .html</button>
        </span>
      </div>
      <iframe title="研究报告" :srcdoc="reportHtml" />
    </section>
  </main>

  <footer>深度研究助手 · MVP · 过程实时直播,结论可追溯</footer>
</template>

<style>
* { box-sizing: border-box; }
body {
  margin: 0; background: #f3f4f6; color: #1f2933;
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif;
}
header { background: #fff; border-bottom: 1px solid #e5e7eb; padding: 20px 16px; }
header .inner { max-width: 900px; margin: 0 auto; }
header h1 { margin: 0; font-size: 22px; }
header p { margin: 4px 0 0; color: #6b7280; font-size: 13.5px; }
main { max-width: 900px; margin: 20px auto 40px; padding: 0 16px; display: flex; flex-direction: column; gap: 16px; }
.card { background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px 20px;
        box-shadow: 0 1px 3px rgba(16, 24, 40, .05); }
.input-row { display: flex; gap: 10px; }
.input-row input { flex: 1; padding: 10px 14px; font-size: 15px; border: 1px solid #d1d5db; border-radius: 8px; outline: none; }
.input-row input:focus { border-color: #2563eb; box-shadow: 0 0 0 3px rgba(37, 99, 235, .12); }
.input-row button { padding: 10px 20px; font-size: 15px; border: none; border-radius: 8px; cursor: pointer;
                    background: #2563eb; color: #fff; white-space: nowrap; }
.input-row button:hover:not(:disabled) { background: #1d4ed8; }
.input-row button:disabled { background: #9ca3af; cursor: not-allowed; }
button.ghost { background: #fff; color: #374151; border: 1px solid #d1d5db; padding: 6px 14px; font-size: 13.5px;
               border-radius: 8px; cursor: pointer; }
button.ghost:hover { background: #f3f4f6; }
.progress-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.status { font-size: 14px; font-weight: 600; color: #2563eb; }
.status.err { color: #dc2626; }
.status.ok { color: #059669; }
.timer { font-size: 12.5px; color: #9ca3af; font-variant-numeric: tabular-nums; }
.log { max-height: 340px; overflow-y: auto; font-size: 13px; line-height: 1.9;
       font-family: "SF Mono", Menlo, Consolas, monospace; padding-right: 6px; }
.log .line { white-space: pre-wrap; word-break: break-all; }
.log .divider { font-weight: 700; color: #111827; border-top: 1px dashed #d1d5db; padding-top: 6px; margin-top: 6px; }
.log .plan { color: #7c3aed; }
.log .query { color: #2563eb; }
.log .hit { color: #0891b2; }
.log .read { color: #059669; }
.log .warn { color: #d97706; }
.log .error { color: #dc2626; font-weight: 600; }
.log .info { color: #6b7280; }
.report-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; flex-wrap: wrap; gap: 8px; }
.report-head .title { font-size: 16px; font-weight: 700; }
.report-head iframe, iframe[title="研究报告"] { width: 100%; height: 72vh; border: 1px solid #e5e7eb; border-radius: 8px; background: #fff; }
footer { text-align: center; color: #9ca3af; font-size: 12.5px; padding: 0 16px 24px; }
</style>
