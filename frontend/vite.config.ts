import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// `npm run dev` proxies the API to a running backend (PORTAL_API, default localhost:8040).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { '/api': process.env.PORTAL_API ?? 'http://localhost:8040' },
  },
});
