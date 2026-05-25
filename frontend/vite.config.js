import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: process.env.VITE_CONTROL_PROXY || "http://127.0.0.1:8100", changeOrigin: true },
      "/v1": { target: process.env.VITE_GATEWAY_PROXY || "http://127.0.0.1:8300", changeOrigin: true },
      "/health": { target: process.env.VITE_GATEWAY_PROXY || "http://127.0.0.1:8300", changeOrigin: true },
      "/ws": { target: process.env.VITE_CONTROL_PROXY || "http://127.0.0.1:8100", changeOrigin: true, ws: true },
    },
  },
});
