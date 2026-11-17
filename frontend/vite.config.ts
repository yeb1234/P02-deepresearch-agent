import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import tailwindcss from "@tailwindcss/vite";

// 开发代理默认指向宿主机后端 12409；容器内或其他环境可用环境变量覆盖。
const proxyTarget =
  process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:12409";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "/app/",
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    proxy: {
      // 将所有请求代理到 LangGraph runtime（含 FastAPI app + API server）
      // 统一使用同一个源，确保 Cookie 自动携带
      "/api": {
        target: proxyTarget,
        changeOrigin: true,
      },
      "/threads": {
        target: proxyTarget,
        changeOrigin: true,
      },
      "/runs": {
        target: proxyTarget,
        changeOrigin: true,
      },
      "/assistants": {
        target: proxyTarget,
        changeOrigin: true,
      },
    },
  },
  build: {
    // 首屏不再把 Markdown、LangGraph SDK 和 Radix 全部打进一个 chunk。
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom"],
          markdown: ["react-markdown", "remark-gfm"],
          langgraph: ["@langchain/langgraph-sdk"],
        },
      },
    },
  },
});
