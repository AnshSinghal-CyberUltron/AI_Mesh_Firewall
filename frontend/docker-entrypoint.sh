#!/bin/sh
set -e

apk add --no-cache wget >/dev/null 2>&1 || true

# Respond on :5173 immediately so the browser never hangs on a blank tab.
node <<'EOF' &
const http = require("http");
const html = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>Starting…</title>
<meta http-equiv="refresh" content="5"></head><body style="font-family:system-ui,sans-serif;padding:2rem">
<h2>AI Mesh Firewall UI is starting</h2><p>Installing dependencies and launching Vite (first boot can take 1–2 minutes).</p>
<p>This page refreshes automatically every 5 seconds.</p></body></html>`;
http.createServer((_, res) => {
  res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
  res.end(html);
}).listen(5173, "0.0.0.0");
EOF
PLACEHOLDER_PID=$!

if [ ! -x node_modules/.bin/vite ]; then
  echo "[frontend] Installing npm dependencies (first run only)..."
  npm ci --no-audit --no-fund || npm install --no-audit --no-fund
fi

kill "$PLACEHOLDER_PID" 2>/dev/null || true
sleep 1

echo "[frontend] Starting Vite on 0.0.0.0:5173 (host :8180)..."
exec npm run dev
