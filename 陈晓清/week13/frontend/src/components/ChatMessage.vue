<script setup>
import { computed, ref } from "vue";

const props = defineProps({
  message: {
    type: Object,
    required: true,
  },
});

const copied = ref(false);
const result = computed(() => props.message.result || null);

async function copySql() {
  if (!result.value?.sql) return;
  try {
    await navigator.clipboard.writeText(result.value.sql);
    copied.value = true;
    setTimeout(() => (copied.value = false), 1500);
  } catch {
    // 浏览器不允许写剪贴板时静默失败。
  }
}
</script>

<template>
  <article class="message" :class="message.role">
    <div class="avatar">{{ message.role === "user" ? "你" : "AI" }}</div>

    <div class="bubble">
      <div v-if="message.pending" class="typing" aria-label="Agent 正在查询">
        <span></span><span></span><span></span>
      </div>

      <p v-else-if="message.role === 'user'" class="user-text">
        {{ message.content }}
      </p>

      <template v-else>
        <div v-if="message.error" class="alert error">
          {{ message.error }}
        </div>

        <template v-else-if="result">
          <p class="answer">{{ result.answer }}</p>

          <div class="chips">
            <span v-if="result.tables?.length" class="chip">
              表：{{ result.tables.join("、") }}
            </span>
            <span class="chip">返回 {{ result.row_count }} 行</span>
            <span v-if="result.repair_attempts" class="chip">
              自动修复 {{ result.repair_attempts }} 次
            </span>
            <span v-if="message.elapsed_ms != null" class="chip">
              {{ (message.elapsed_ms / 1000).toFixed(1) }}s
            </span>
            <span v-if="result.truncated" class="chip warn">结果已截断</span>
          </div>

          <div v-if="result.error" class="alert error">
            执行提示：{{ result.error }}
          </div>

          <details v-if="result.sql" class="sql-card">
            <summary>查看生成的 SQL</summary>
            <div class="sql-head">
              <span>SQLite</span>
              <button type="button" @click="copySql">
                {{ copied ? "已复制" : "复制" }}
              </button>
            </div>
            <pre><code>{{ result.sql }}</code></pre>
          </details>

          <div v-if="result.evidence?.length" class="section">
            <div class="section-title">数据依据</div>
            <ul>
              <li v-for="(item, index) in result.evidence" :key="index">
                {{ item }}
              </li>
            </ul>
          </div>

          <div v-if="result.caveats?.length" class="section caveats">
            <div class="section-title">注意事项</div>
            <ul>
              <li v-for="(item, index) in result.caveats" :key="index">
                {{ item }}
              </li>
            </ul>
          </div>
        </template>
      </template>
    </div>
  </article>
</template>
