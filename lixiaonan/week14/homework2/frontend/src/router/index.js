// 路由：/documents 文档上传+解析状态列表；/chat 多模态问答
import { createRouter, createWebHistory } from 'vue-router'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/documents' },
    { path: '/documents', component: () => import('../views/DocumentListView.vue') },
    { path: '/chat', component: () => import('../views/ChatView.vue') }
  ]
})
