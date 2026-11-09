import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发模式:/api 代理到本机 FastAPI 后端,SSE 事件流直接透传
// 6174 固定端口(strictPort):避开本机其他 Vite 项目的常用端口
export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',   // 显式绑 IPv4:默认 localhost 会绑到 ::1,导致 127.0.0.1 访问不通
    port: 6174,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:7080',
        changeOrigin: true,
      },
    },
  },
})
