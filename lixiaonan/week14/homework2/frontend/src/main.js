// 前端入口：注册 Vue、Element Plus、路由
import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import router from './router'

createApp(App).use(router).use(ElementPlus).mount('#app')
