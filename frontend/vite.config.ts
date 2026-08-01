import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    port: 5173,
    // 개발 중에는 프론트가 /api를 백엔드로 프록시하므로 CORS 설정이 필요 없다.
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET ?? 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
