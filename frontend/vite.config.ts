import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import fs from "fs";
import path from "path";

function readVersionFromRoot(): string {
  try {
    // 从当前工作目录开始向上查找 VERSION 文件，兼容本地开发和 Docker 构建
    let currentDir = process.cwd();
    // 防止死循环，最多向上查找几级目录
    for (let i = 0; i < 5; i += 1) {
      const candidate = path.join(currentDir, "VERSION");
      if (fs.existsSync(candidate)) {
        const content = fs.readFileSync(candidate, "utf-8").trim();
        return content || "dev";
      }
      const parent = path.dirname(currentDir);
      if (parent === currentDir) break;
      currentDir = parent;
    }
  } catch {
    // ignore
  }
  return "dev";
}

const appVersion = readVersionFromRoot();

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
  },
  build: {
    chunkSizeWarningLimit: 1000,
    rollupOptions: {
      output: {
        manualChunks: undefined,
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://backend:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
