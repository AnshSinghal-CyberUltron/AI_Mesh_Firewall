"""Local fake MCP server (streamable-http, JSON replies). Requires `Authorization: Bearer upstream-secret`
on EVERY request; logs each request (method, auth ok?) to stdout so the evidence shows what the
gateway actually sent upstream."""
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
TOKEN = "Bearer upstream-secret"
TOOLS = [
 {"name": "echo", "description": "echo text", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
 {"name": "get_contact", "description": "returns a contact", "inputSchema": {"type": "object", "properties": {}}},
 {"name": "danger", "description": "second tool (for allow-list checks)", "inputSchema": {"type": "object", "properties": {}}},
]
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, obj, extra=None):
        b = json.dumps(obj).encode(); self.send_response(code)
        self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(b)))
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        n = int(self.headers.get("content-length") or 0); raw = self.rfile.read(n)
        try: req = json.loads(raw)
        except Exception: req = {}
        ok = self.headers.get("authorization") == TOKEN
        print(json.dumps({"method": req.get("method"), "auth_ok": ok, "path": self.path}), flush=True)
        if not ok: return self._send(401, {"error": "unauthorized"})
        m, i = req.get("method"), req.get("id")
        if m == "initialize":
            return self._send(200, {"jsonrpc": "2.0", "id": i, "result": {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake-mcp", "version": "1"}}}, {"mcp-session-id": "fake-session-1"})
        if m and m.startswith("notifications/"):
            self.send_response(202); self.send_header("content-length", "0"); self.end_headers(); return
        if m == "ping": return self._send(200, {"jsonrpc": "2.0", "id": i, "result": {}})
        if m == "tools/list": return self._send(200, {"jsonrpc": "2.0", "id": i, "result": {"tools": TOOLS}})
        if m == "tools/call":
            name = (req.get("params") or {}).get("name"); args = (req.get("params") or {}).get("arguments") or {}
            if name == "get_contact": text = "Contact: carol.jones@corp.com, key AKIAIOSFODNN7EXAMPLE"
            elif name == "echo": text = "echo: " + str(args.get("text", ""))
            else: text = "danger executed"
            return self._send(200, {"jsonrpc": "2.0", "id": i, "result": {"content": [{"type": "text", "text": text}], "isError": False}})
        return self._send(200, {"jsonrpc": "2.0", "id": i, "error": {"code": -32601, "message": "method not found"}})
    def do_GET(self): self._send(405, {"error": "no sse"})
    def do_DELETE(self): self._send(200, {})
ThreadingHTTPServer(("0.0.0.0", 9500), H).serve_forever()
