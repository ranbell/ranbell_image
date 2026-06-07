import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: 3100,
    proxy: {
      '/api': { target: 'http://backend:8001', changeOrigin: true },
    },
    watch: {
      usePolling: true,
    },
  },
})
