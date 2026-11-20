<template>
  <div class="md-renderer" v-html="renderedHtml"></div>
</template>

<script setup>
// Markdown 图文混排渲染（F3-5；M3 先用于解析结果预览 F2-5，M4 复用于问答回答）
// marked 解析 + DOMPurify 消毒，图片自适应宽度（v-html 内容需用非 scoped 样式）
import { computed } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const props = defineProps({
  markdown: { type: String, default: '' },
})

const renderedHtml = computed(() => {
  if (!props.markdown) return '<p style="color:#999">（暂无内容）</p>'
  const html = marked.parse(props.markdown, { breaks: true, async: false })
  return DOMPurify.sanitize(html)
})
</script>

<style>
.md-renderer { line-height: 1.7; word-break: break-word; }
.md-renderer img { max-width: 100%; max-height: 480px; border-radius: 6px; margin: 8px 0; }
.md-renderer pre { background: #f5f7fa; padding: 12px; border-radius: 6px; overflow: auto; }
.md-renderer code { background: #f5f7fa; padding: 2px 5px; border-radius: 4px; }
.md-renderer table { border-collapse: collapse; margin: 8px 0; }
.md-renderer th, .md-renderer td { border: 1px solid #dcdfe6; padding: 6px 12px; }
.md-renderer h1, .md-renderer h2, .md-renderer h3 { margin: 12px 0 8px; }
</style>
