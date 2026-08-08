import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev-only proxy: `npm run dev` serves the frontend on its own port, so
// same-origin /api calls (the production topology, see webapp/main.py)
// need a proxy to reach the FastAPI dev server locally.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // 127.0.0.1, not 'localhost': Node resolves 'localhost' to ::1 first,
      // but uvicorn's default --host 0.0.0.0 only binds IPv4, so the
      // ambiguous form intermittently 502s depending on resolution order.
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
