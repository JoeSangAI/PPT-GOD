import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  // Browser project links are real nested routes (/app/projects/:id).
  // Root-relative assets keep a direct navigation from resolving JS/CSS under
  // /app/projects/assets, where the SPA fallback would return HTML instead.
  base: '/',
  plugins: [react()],
  build: {
    outDir: 'dist',
  },
})
