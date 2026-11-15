import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 后端端口与本文件保持一致（见 apps/server/app/config.py 的 APP_PORT）
const BACKEND = "http://127.0.0.1:8710";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": { target: BACKEND, changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: false },
});
