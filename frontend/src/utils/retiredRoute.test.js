import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  isRetiredResponse,
  retiredFromMessage,
  retiredFromResponse,
  retiredPanelModel,
} from "./retiredRoute.js";

const envelope = {
  status: "error",
  data: { code: "retired_on_gateway_v2", message: "No breaker on v2.", path: "/v1/admin/db-test" },
};

test("GW35: only a 410 with the retired code is the retired answer", () => {
  assert.equal(isRetiredResponse(410, envelope), true);
  assert.equal(isRetiredResponse(200, envelope), false);
  assert.equal(isRetiredResponse(410, { data: { code: "other" } }), false);
  assert.equal(isRetiredResponse(410, null), false);
});

test("GW35: the panel model carries the reason and the path", () => {
  assert.deepEqual(retiredPanelModel(envelope), {
    title: "Retired on gateway v2",
    message: "No breaker on v2.",
    path: "/v1/admin/db-test",
  });
});

test("GW35: retiredFromResponse reads a clone and leaves the body to the caller", async () => {
  const res = new Response(JSON.stringify(envelope), { status: 410 });
  const model = await retiredFromResponse(res);
  assert.equal(model.message, "No breaker on v2.");
  assert.deepEqual(await res.json(), envelope);
  assert.equal(await retiredFromResponse(new Response("{}", { status: 200 })), null);
});

test("GW35: a retired discovery arrives as a prefixed sync error string", () => {
  const model = retiredFromMessage("retired_on_gateway_v2: tool discovery is not served.");
  assert.equal(model.message, "tool discovery is not served.");
  assert.equal(retiredFromMessage("gateway HTTP 500"), null);
  assert.equal(retiredFromMessage(undefined), null);
});

test("GW35: each page that calls a retired route imports the retired-route helper", () => {
  const files = [
    "simulator/CircuitBreakerSimulator.jsx",
    "simulator/IsolationOpsSimulator.jsx",
    "BedrockTestPanel.jsx",
    "DatabaseConnectionPanel.jsx",
    "MCPConnectorPanel.jsx",
    "MCPManagerPanel.jsx",
  ];
  for (const file of files) {
    const source = readFileSync(new URL(`../components/${file}`, import.meta.url), "utf8");
    assert.match(source, /retiredRoute\.js/, file);
    assert.match(source, /RetiredOnGatewayV2/, file);
  }
});
