import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

const backendHttpTarget = process.env.VITE_BACKEND_TARGET || 'http://127.0.0.1:8000'
const backendWsTarget = process.env.VITE_BACKEND_WS_TARGET || backendHttpTarget.replace(/^http/, 'ws')

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, 'src') },
  },
  server: {
    port: 5178,
    proxy: {
      '/api': {
        target: backendHttpTarget,
        changeOrigin: true,
      },
      '/ws': {
        target: backendWsTarget,
        ws: true,
      },
    },
  },
})
