#!/usr/bin/env node
/** Minimal MCP-over-WebSocket echo stub for P4.13 four-transport verify (Cursor-owned). */
import { createServer } from "node:http";
import { WebSocketServer } from "ws";

const PORT = Number(process.env.PORT || 3003);

function handleMessage(ws, raw) {
  let msg;
  try {
    msg = JSON.parse(raw);
  } catch {
    return;
  }
  const { method, id, params } = msg;
  if (method === "notifications/initialized") {
    return;
  }
  let result;
  if (method === "initialize") {
    result = {
      protocolVersion: "2024-11-05",
      capabilities: { tools: {} },
      serverInfo: { name: "ws-everything-stub", version: "0.1.0" },
    };
  } else if (method === "tools/list") {
    result = {
      tools: [
        {
          name: "echo",
          description: "Echo a message back",
          inputSchema: {
            type: "object",
            properties: { message: { type: "string" } },
            required: ["message"],
          },
        },
      ],
    };
  } else if (method === "tools/call") {
    const message = params?.arguments?.message ?? "";
    result = { content: [{ type: "text", text: `Echo: ${message}` }] };
  } else {
    return;
  }
  if (id !== undefined) {
    ws.send(JSON.stringify({ jsonrpc: "2.0", id, result }));
  }
}

const httpServer = createServer();
const wss = new WebSocketServer({ server: httpServer, path: "/mcp" });
wss.on("connection", (ws) => {
  ws.on("message", (data) => handleMessage(ws, data.toString()));
});
httpServer.listen(PORT, "0.0.0.0", () => {
  console.log(`ws-everything-stub listening ws://0.0.0.0:${PORT}/mcp`);
});
