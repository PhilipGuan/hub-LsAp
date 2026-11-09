<script setup>
import { computed, nextTick, onMounted, ref, watch } from "vue";

import ChatMessage from "./components/ChatMessage.vue";
import { chat, deleteSession, getSession, health } from "./api";

const STORAGE_KEY = "sql-agent-session-id";
const examples = [
  "数据库里一共有多少首歌曲？",
  "销售额最高的前 5 位客户是谁？",
  "每个国家的客户数量是多少？",
];

const messages = ref([]);
const sessionId = ref(localStorage.getItem(STORAGE_KEY));
const input = ref("");
const loading = ref(false);
const booting = ref(true);
const healthInfo = ref(null);
const online = ref(false);
const listEl = ref(null);
const textareaEl = ref(null);

const canSend = computed(() => input.value.trim().length > 0 && !loading.value);

function newId() {
  return crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function replaceMessage(id, next) {
  const index = messages.value.findIndex((message) => message.id === id);
  if (index !== -1) messages.value[index] = next;
}

async function scrollToBottom() {
  await nextTick();
  const el = listEl.value;
  if (el) el.scrollTop = el.scrollHeight;
}

function clearSession() {
  sessionId.value = null;
  localStorage.removeItem(STORAGE_KEY);
}

function historyToMessages(turns) {
  return turns.flatMap((turn, index) => [
    {
      id: `${index}-user`,
      role: "user",
      content: turn.question,
    },
    {
      id: `${index}-assistant`,
      role: "assistant",
      result: turn.result,
      created_at: turn.created_at,
    },
  ]);
}

async function refreshHealth() {
  try {
    healthInfo.value = await health();
    online.value = true;
  } catch {
    online.value = false;
  }
}

onMounted(async () => {
  await refreshHealth();

  if (sessionId.value) {
    try {
      const data = await getSession(sessionId.value);
      messages.value = historyToMessages(data.turns);
    } catch {
      clearSession();
    }
  }

  booting.value = false;
  await scrollToBottom();
});

async function send(text) {
  const question = (typeof text === "string" ? text : input.value).trim();
  if (!question || loading.value) return;

  loading.value = true;
  input.value = "";

  messages.value.push({ id: newId(), role: "user", content: question });
  const assistantId = newId();
  messages.value.push({ id: assistantId, role: "assistant", pending: true });
  await scrollToBottom();

  try {
    const data = await chat(question, sessionId.value);
    sessionId.value = data.session_id;
    localStorage.setItem(STORAGE_KEY, data.session_id);
    online.value = true;
    replaceMessage(assistantId, {
      id: assistantId,
      role: "assistant",
      result: data.result,
      elapsed_ms: data.elapsed_ms,
    });
  } catch (error) {
    replaceMessage(assistantId, {
      id: assistantId,
      role: "assistant",
      error: error.message || "请求失败，请检查后端是否已启动。",
    });
  } finally {
    loading.value = false;
    await scrollToBottom();
  }
}

async function startNewSession() {
  if (loading.value) return;

  const current = sessionId.value;
  clearSession();
  messages.value = [];

  if (current) {
    try {
      await deleteSession(current);
    } catch {
      // 会话可能已经在服务端过期，忽略即可。
    }
  }

  await nextTick();
  textareaEl.value?.focus();
}

function onKeydown(event) {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    send();
  }
}

function resizeInput() {
  const el = textareaEl.value;
  if (!el) return;
  el.style.height = "auto";
  el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
}

watch(input, () => nextTick(resizeInput));
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand">
        <div class="logo">SQL</div>
        <div>
          <h1>Chinook SQL Agent</h1>
          <p>用自然语言查询数据库，自动选表、生成并执行 SQL</p>
        </div>
      </div>

      <div class="topbar-actions">
        <button
          class="status"
          :class="{ offline: !online }"
          type="button"
          :title="healthInfo?.database || '点击重试连接'"
          @click="refreshHealth"
        >
          <i></i>
          {{ online ? healthInfo?.model || "已连接" : "后端未连接" }}
        </button>
        <button
          class="ghost"
          type="button"
          :disabled="loading || messages.length === 0"
          @click="startNewSession"
        >
          新对话
        </button>
      </div>
    </header>

    <main class="chat-panel">
      <div ref="listEl" class="message-list" aria-live="polite">
        <div v-if="booting" class="center-state">正在载入会话…</div>

        <div v-else-if="messages.length === 0" class="empty-state">
          <div class="empty-icon">⌘</div>
          <h2>从一个问题开始</h2>
          <p>Agent 会自己选择数据表、生成 SQL、执行查询，再用自然语言总结结果。</p>
          <div class="examples">
            <button
              v-for="example in examples"
              :key="example"
              type="button"
              @click="send(example)"
            >
              {{ example }}
            </button>
          </div>
        </div>

        <ChatMessage
          v-for="message in messages"
          :key="message.id"
          :message="message"
        />
      </div>

      <form class="composer" @submit.prevent="send()">
        <textarea
          ref="textareaEl"
          v-model="input"
          rows="1"
          aria-label="输入查询问题"
          placeholder="例如：销售额最高的前 5 位客户是谁？"
          :disabled="loading"
          @keydown="onKeydown"
        ></textarea>
        <button type="submit" :disabled="!canSend">
          {{ loading ? "查询中…" : "发送" }}
        </button>
      </form>
      <p class="hint">Enter 发送，Shift + Enter 换行</p>
    </main>
  </div>
</template>
