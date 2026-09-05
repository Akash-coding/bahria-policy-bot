import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        timeout: 300000,
        proxyTimeout: 300000,
      },
      "/assets/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        timeout: 300000,
        proxyTimeout: 300000,
        rewrite: (path) => path.replace(/^\/assets\/api/, "/api"),
      },
      "/media": "http://127.0.0.1:8000",
      "/django-admin": "http://127.0.0.1:8000",
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
