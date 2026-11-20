import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发代理：/api 与 /static（解析图片）转发到后端 FastAPI
// host: true        —— 绑定 0.0.0.0，允许云开发机外部（SSH 隧道/平台端口映射）访问
// allowedHosts: true —— 放行任意 Host 头（平台分配的临时公网域名访问 dev server 时必需）
export default defineConfig({
  plugins: [vue()],
  server: {
    host: true,
    port: 5173,
    allowedHosts: true,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/static': { target: 'http://localhost:8000', changeOrigin: true }
    }
  }
})
