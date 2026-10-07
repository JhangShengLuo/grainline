import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// 開發時 /api 轉到本機的 FastAPI；docker compose 裡由 nginx 轉發
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: process.env.API_URL ?? "http://localhost:8000",
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    environment: "node",
  },
});
