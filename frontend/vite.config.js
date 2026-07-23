import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import tailwindcss from "@tailwindcss/vite";

// `agent: false` disables HTTP keep-alive on the proxy, forcing a fresh TCP
// connection (and therefore a fresh DNS lookup) per request. Without this the
// dev server caches the upstream container's IP at first-resolve, then keeps
// the dead socket pool open after `docker compose restart <upstream>` and
// returns ECONNREFUSED until the frontend container is also restarted.
const noKeepAlive = { agent: false };

/** nginx parity: `/demo` → `/demo/` so relative assets + API_BASE stay under `/demo/`. */
function demoTrailingSlashRedirect() {
  return {
    name: "demo-trailing-slash-redirect",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const raw = req.url || "";
        const q = raw.indexOf("?");
        const path = q >= 0 ? raw.slice(0, q) : raw;
        const query = q >= 0 ? raw.slice(q) : "";
        if (path === "/demo") {
          res.statusCode = 308;
          res.setHeader("Location", `/demo/${query}`);
          res.end();
          return;
        }
        next();
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), demoTrailingSlashRedirect()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    allowedHosts: true,
    // Docker Desktop on Windows bind-mounts need polling for reliable HMR.
    // Aggressive 1s polls across the whole tree stampede FSWatcher into EIO and
    // kill Vite mid-lazy-import; ignore tests/docs and poll less often.
    watch: {
      usePolling: true,
      interval: 2000,
      ignored: [
        "**/node_modules/**",
        "**/.git/**",
        "**/*.{test,spec}.{js,jsx,ts,tsx}",
        "**/readme.md",
        "**/README.md",
        "**/public/**",
        "**/dist/**",
      ],
    },
    proxy: {
      "/api": { target: process.env.VITE_CONTROL_PROXY || "http://127.0.0.1:8100", changeOrigin: true, ...noKeepAlive },
      "/v1": { target: process.env.VITE_GATEWAY_PROXY || "http://127.0.0.1:8300", changeOrigin: true, ...noKeepAlive },
      "/health": { target: process.env.VITE_GATEWAY_PROXY || "http://127.0.0.1:8300", changeOrigin: true, ...noKeepAlive },
      "/gw-health": { target: process.env.VITE_GATEWAY_PROXY || "http://127.0.0.1:8300", rewrite: (p) => p.replace(/^\/gw-health/, "/health"), ...noKeepAlive },
      // OpenAI-SDK demo app (examples/zeroshield-openai-demo) — strip /demo prefix like nginx.
      "/demo": {
        target: process.env.VITE_DEMO_PROXY || "http://127.0.0.1:8770",
        changeOrigin: true,
        rewrite: (p) => {
          const stripped = p.replace(/^\/demo/, "");
          return stripped.length ? stripped : "/";
        },
        ...noKeepAlive,
      },
      "/ws": { target: process.env.VITE_CONTROL_PROXY || "http://127.0.0.1:8100", changeOrigin: true, ws: true, ...noKeepAlive },
    },
  },
});
