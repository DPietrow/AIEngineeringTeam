import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Forward API calls (including SSE streams) to the Flask backend in dev.
    proxy: {
      '/api': 'http://127.0.0.1:5000',
    },
  },
})
