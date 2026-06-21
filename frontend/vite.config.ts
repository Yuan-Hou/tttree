import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// 后端 FastAPI 跑在 8000。开发期把所有 API 路径 proxy 过去,避免 CORS;
// 部署期 `npm run build` 产物由 FastAPI 在 /app 下托管(见 backend/app/main.py)。
const API = "http://localhost:8000";
const proxy = (path: string) => ({ [path]: { target: API, changeOrigin: true } });

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // 构建产物被 FastAPI 挂在 /app 下,故用相对 base。
  base: "./",
  server: {
    host: true, // 0.0.0.0 — 让 Windows 浏览器经 WSL 访问
    port: 5173,
    // WSL2 下代码在 Windows 盘(/mnt/e)上,inotify 不触发 → HMR 收不到改动。
    // 用轮询监听,确保热更新可靠(代价是后台轮询,开发期可接受)。
    watch: { usePolling: true, interval: 300 },
    proxy: {
      ...proxy("/stories"),
      ...proxy("/story"),
      ...proxy("/storage"),
      ...proxy("/health"),
      ...proxy("/auth"),
      ...proxy("/global-settings"),
    },
  },
});
