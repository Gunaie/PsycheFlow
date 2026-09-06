import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发期把 /api 反代到后端容器，避免跨域
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_PROXY_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
