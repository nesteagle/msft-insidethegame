import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API runs separately (`footballfoundry-agents serve`, port 8100); in production the agents
// service serves the built app itself, so the page and the API share one origin.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: process.env.FF_API ?? "http://127.0.0.1:8100", changeOrigin: true } },
  },
  build: { outDir: "dist", sourcemap: true },
});
