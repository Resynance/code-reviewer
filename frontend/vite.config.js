import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In dev, the React app runs on Vite's port and proxies API + webhook calls to
// the FastAPI server on :1500. In production, FastAPI serves the built bundle
// from frontend/dist and these proxies are irrelevant.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:1500',
      '/webhook': 'http://localhost:1500',
    },
  },
  build: {
    outDir: 'dist',
  },
})
