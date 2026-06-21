import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 登录前端(独立项目,多前端·统一后端架构的第一块)。开发期 :5174,把 /auth 代理到后端 :8000。
// 部署期 `npm run build` 产物可由后端挂在 /login 下(与 API 同源)。base 相对,便于挂任意路径前缀。
const API = "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    host: true, // 0.0.0.0 — 让 Windows 浏览器经 WSL 访问
    port: 5174,
    watch: { usePolling: true, interval: 300 }, // WSL2 /mnt 盘:inotify 不触发,轮询兜底
    proxy: {
      "/auth": { target: API, changeOrigin: true },
      "/admin": { target: API, changeOrigin: true },
      "/brand": { target: API, changeOrigin: true },
    },
  },
});
