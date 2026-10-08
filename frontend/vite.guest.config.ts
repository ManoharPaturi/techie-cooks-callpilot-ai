import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Guest bundle: built separately so it can never contain host code, transcripts or AI UI.
export default defineConfig({
  plugins: [react()],
  base: '/',
  build: {
    outDir: 'dist-guest',
    assetsDir: 'guest-assets',
    emptyOutDir: true,
    assetsInlineLimit: 0,
    rollupOptions: { input: { guest: 'guest/index.html' } },
  },
});
