import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      // 开发时把 /api 转发到 FastAPI，前后端同源，完全不涉及跨域。
      // 后端同时也开着 CORS，所以直连（VITE_API_BASE_URL 指向后端）也能用。
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
