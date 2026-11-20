<template>
  <section class="doc-page">
    <!-- 功能1（F1-1~F1-3）：拖拽多选上传 PDF -->
    <el-card shadow="never" class="upload-card">
      <template #header><b>上传 PDF 文档</b></template>
      <el-upload
        v-model:file-list="fileList"
        drag
        multiple
        accept=".pdf"
        :auto-upload="false"
      >
        <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
        <div class="el-upload__text">拖拽 PDF 到此处，或 <em>点击选择文件</em></div>
        <template #tip>
          <div class="el-upload__tip">
            支持多选，仅 .pdf；单个 ≤ {{ MAX_UPLOAD_SIZE_MB }}MB，单次 ≤ {{ MAX_UPLOAD_COUNT }} 个
          </div>
        </template>
      </el-upload>
      <div class="upload-actions">
        <el-button
          type="primary"
          :loading="uploading"
          :disabled="!fileList.length"
          @click="handleUpload"
        >
          开始上传{{ fileList.length ? `（${fileList.length} 个文件）` : '' }}
        </el-button>
      </div>
    </el-card>

    <!-- 功能2（F2-1~F2-2）：解析状态表格，3 秒轮询刷新 -->
    <el-card shadow="never">
      <template #header>
        <div class="table-header">
          <b>文档列表</b>
          <span class="table-sub">
            <el-icon class="is-loading"><Refresh /></el-icon>
            每 {{ POLL_INTERVAL_MS / 1000 }} 秒自动刷新
          </span>
        </div>
      </template>

      <el-table :data="docs" v-loading="loading" stripe>
        <el-table-column prop="file_name" label="文件名" min-width="180" show-overflow-tooltip />
        <el-table-column label="大小" width="90" align="center">
          <template #default="{ row }">{{ formatSize(row.size) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="110" align="center">
          <template #default="{ row }">
            <StatusTag :status="row.status" />
          </template>
        </el-table-column>
        <el-table-column label="页数" width="70" align="center">
          <template #default="{ row }">{{ row.page_count ?? '-' }}</template>
        </el-table-column>
        <el-table-column label="解析耗时" width="90" align="center">
          <template #default="{ row }">
            {{ row.duration_seconds != null ? row.duration_seconds + 's' : '-' }}
          </template>
        </el-table-column>
        <el-table-column label="失败原因" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="fail-reason">{{ row.fail_reason || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="上传时间" width="170">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="180" align="center">
          <template #default="{ row }">
            <el-button
              link type="primary"
              :disabled="row.status !== 'SUCCESS'"
              @click="openPreview(row)"
            >查看解析结果</el-button>
            <el-button
              link type="warning"
              :disabled="row.status !== 'FAILED'" :loading="reparsingId === row.id"
              @click="handleReparse(row)"
            >重新解析</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-bar">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next"
          @current-change="() => refresh(true)"
          @size-change="() => { page = 1; refresh(true) }"
        />
      </div>
    </el-card>

    <!-- F2-5：解析结果 Markdown 预览（图片经 vite 代理访问后端 /static） -->
    <el-dialog v-model="previewVisible" :title="`解析结果预览 - ${previewFileName}`" width="72%" top="5vh">
      <div class="preview-body" v-loading="previewLoading">
        <MarkdownRenderer :markdown="previewMarkdown" />
      </div>
    </el-dialog>
  </section>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, UploadFilled } from '@element-plus/icons-vue'

import {
  getDocumentMarkdown, listDocuments, reparseDocument, uploadDocuments,
} from '../api'
import MarkdownRenderer from '../components/MarkdownRenderer.vue'
import StatusTag from '../components/StatusTag.vue'

const POLL_INTERVAL_MS = 3000
const MAX_UPLOAD_SIZE_MB = 50
const MAX_UPLOAD_COUNT = 10

// ---------- 上传（F1-1~F1-3） ----------
const fileList = ref([])          // el-upload 待上传文件（raw 为原始 File）
const uploading = ref(false)

async function handleUpload() {
  if (!fileList.value.length) return
  if (fileList.value.length > MAX_UPLOAD_COUNT) {
    ElMessage.warning(`单次最多上传 ${MAX_UPLOAD_COUNT} 个文件`)
    return
  }
  uploading.value = true
  try {
    const results = await uploadDocuments(fileList.value.map((f) => f.raw))
    const okCount = results.filter((r) => r.status === 'ok').length
    for (const item of results.filter((r) => r.status !== 'ok')) {
      ElMessage.error(`${item.file_name}: ${item.message}`)
    }
    if (okCount) {
      ElMessage.success(`已上传 ${okCount} 个文件，解析任务已加入队列`)
      fileList.value = []
    }
    await refresh(true)
  } catch (e) {
    ElMessage.error(`上传失败: ${e.message}`)
  } finally {
    uploading.value = false
  }
}

// ---------- 状态列表与轮询（F2-1~F2-2） ----------
const docs = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const loading = ref(false)
let timer = null

async function refresh(showLoading = false) {
  if (showLoading) loading.value = true
  try {
    const data = await listDocuments({ page: page.value, page_size: pageSize.value })
    docs.value = data.items
    total.value = data.total
  } catch (e) {
    if (showLoading) ElMessage.error(`加载文档列表失败: ${e.message}`)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  refresh(true)
  timer = setInterval(() => refresh(false), POLL_INTERVAL_MS)
})
onUnmounted(() => clearInterval(timer))

// ---------- 操作（F2-5） ----------
const previewVisible = ref(false)
const previewLoading = ref(false)
const previewMarkdown = ref('')
const previewFileName = ref('')
const reparsingId = ref(null)

async function openPreview(row) {
  previewVisible.value = true
  previewLoading.value = true
  previewFileName.value = row.file_name
  previewMarkdown.value = ''
  try {
    const data = await getDocumentMarkdown(row.id)
    previewMarkdown.value = data.markdown
  } catch (e) {
    ElMessage.error(`获取解析结果失败: ${e.message}`)
    previewVisible.value = false
  } finally {
    previewLoading.value = false
  }
}

async function handleReparse(row) {
  reparsingId.value = row.id
  try {
    await reparseDocument(row.id)
    ElMessage.success(`「${row.file_name}」已重新加入解析队列`)
    await refresh(true)
  } catch (e) {
    ElMessage.error(`重新解析失败: ${e.message}`)
  } finally {
    reparsingId.value = null
  }
}

// ---------- 格式化 ----------
function formatSize(bytes) {
  if (bytes == null) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function formatTime(iso) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}
</script>

<style scoped>
.doc-page { max-width: 1100px; margin: 0 auto; display: flex; flex-direction: column; gap: 16px; }
.upload-actions { margin-top: 12px; text-align: right; }
.table-header { display: flex; justify-content: space-between; align-items: center; }
.table-sub { font-size: 12px; color: #909399; display: inline-flex; align-items: center; gap: 4px; }
.fail-reason { color: #f56c6c; }
.pagination-bar { margin-top: 14px; display: flex; justify-content: flex-end; }
.preview-body { max-height: 65vh; overflow: auto; padding: 0 8px; }
</style>
