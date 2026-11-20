<template>
  <el-tag :type="meta.type" :effect="'light'" class="status-tag">
    <el-icon v-if="status === 'PARSING'" class="is-loading" style="margin-right: 4px">
      <Loading />
    </el-icon>
    {{ meta.label }}
  </el-tag>
</template>

<script setup>
// 功能2（F2-1）：解析状态标签
// PENDING->待解析(info) / PARSING->解析中(warning) / SUCCESS->解析完成(success) / FAILED->解析失败(danger)
import { computed } from 'vue'
import { Loading } from '@element-plus/icons-vue'

const props = defineProps({
  status: { type: String, required: true },
})

const META = {
  PENDING: { label: '待解析', type: 'info' },
  PARSING: { label: '解析中', type: 'warning' },
  SUCCESS: { label: '解析完成', type: 'success' },
  FAILED: { label: '解析失败', type: 'danger' },
}

const meta = computed(() => META[props.status] ?? { label: props.status, type: 'info' })
</script>
