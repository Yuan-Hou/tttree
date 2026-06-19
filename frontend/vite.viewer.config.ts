import { defineConfig } from "vite";
import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { viteSingleFile } from "vite-plugin-singlefile";

// 导出查看器的「单文件」构建:把 JS/CSS 全部内联进一个 viewer.html(图片由后端导出时再注入 data: URI)。
// 与主应用构建(vite.config.ts → dist/)分开,互不影响。产物在 dist-viewer/viewer.html,
// 由后端导出接口读取为模板,注入 window.__VORE_EXPORT__ 冻结快照后下发。
export default defineConfig({
  plugins: [react(), tailwindcss(), viteSingleFile()],
  base: "./",
  build: {
    outDir: "dist-viewer",
    emptyOutDir: true,
    rollupOptions: { input: resolve(__dirname, "viewer.html") },
  },
});
