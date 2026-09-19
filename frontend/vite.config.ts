import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vitest/config'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
  },
  test: {
    environment: 'jsdom',
    // Match the dev server's origin so cross-origin requests to the API
    // behave as they do in a browser; jsdom defaults to http://localhost
    // (no port), which the backend's CORS allowlist rejects on preflight.
    environmentOptions: { jsdom: { url: 'http://localhost:5173' } },
    globals: true,
    setupFiles: './src/test/setup.ts',
  },
})
