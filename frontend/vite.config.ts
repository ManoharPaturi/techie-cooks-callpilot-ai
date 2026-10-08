import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Host bundle only. It is served by the private 127.0.0.1 FastAPI app, never by a dev server during judging.
// The guest bundle (Day 2) gets its own build so it can never include host code.
export default defineConfig({
  plugins: [react()],
  base: '/',
  build: {
    outDir: 'dist',
    assetsDir: 'host-assets',
    emptyOutDir: true,
    assetsInlineLimit: 0, // the AudioWorklet must load as a same-origin file, not a data: URL
    rollupOptions: { input: { host: 'host/index.html' } },
  },
});
