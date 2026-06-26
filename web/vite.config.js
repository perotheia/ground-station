import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The UI talks to the GS backend at /api. In dev, proxy it to the local gs-api
// (or the live one via GS_API_TARGET). In prod, nginx serves the build + proxies
// /api to gs-api, so no proxy is needed there.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.GS_API_TARGET || 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
  build: { outDir: 'dist' },
})
