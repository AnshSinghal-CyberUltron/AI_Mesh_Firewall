import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import tailwindcss from "@tailwindcss/vite";

// `agent: false` disables HTTP keep-alive on the proxy, forcing a fresh TCP
// connection (and therefore a fresh DNS lookup) per request. Without this the
// dev server caches the upstream container's IP at first-resolve, then keeps
// the dead socket pool open after `docker compose restart <upstream>` and
// returns ECONNREFUSED until the frontend container is also restarted.
const noKeepAlive = { agent: false };

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: process.env.VITE_CONTROL_PROXY || "http://127.0.0.1:8100", changeOrigin: true, ...noKeepAlive },
      "/v1": { target: process.env.VITE_GATEWAY_PROXY || "http://127.0.0.1:8300", changeOrigin: true, ...noKeepAlive },
      "/health": { target: process.env.VITE_GATEWAY_PROXY || "http://127.0.0.1:8300", changeOrigin: true, ...noKeepAlive },
      "/ws": { target: process.env.VITE_CONTROL_PROXY || "http://127.0.0.1:8100", changeOrigin: true, ws: true, ...noKeepAlive },
    },
  },
});
