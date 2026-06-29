import test from "node:test";
import assert from "node:assert/strict";
import { consumeSSEStream, extractRoutingFromHeaders } from "./liveGateway.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const encoder = new TextEncoder();

/** Build a minimal fetch-Response-like object streaming the given chunks. */
function sseResponse(chunks, contentType = "text/event-stream") {
  let i = 0;
  return {
    headers: new Headers({ "content-type": contentType }),
    body: {
      getReader: () => ({
        read: async () => {
          if (i < chunks.length) {
            const chunk = chunks[i++];
            const value = typeof chunk === "string" ? encoder.encode(chunk) : chunk;
            return { done: false, value };
          }
          return { done: true, value: undefined };
        },
      }),
    },
  };
}

const deltaEvent = (text) =>
  `data: ${JSON.stringify({ choices: [{ delta: { content: text } }] })}`;

// ---------------------------------------------------------------------------
// consumeSSEStream — processPart via complete \n\n-terminated events
// ---------------------------------------------------------------------------

test("parses \\n\\n-terminated data events and [DONE]", async () => {
  const result = await consumeSSEStream(
    sseResponse([`${deltaEvent("Hello")}\n\n${deltaEvent(" world")}\n\ndata: [DONE]\n\n`]),
  );
  assert.equal(result.isStream, true);
  assert.equal(result.aggregatedContent, "Hello world");
  assert.deepEqual(
    result.events.map((e) => e.type),
    ["data", "data", "done"],
  );
  assert.equal(result.terminalError, null);
});

test("buffers an event split across reader chunks", async () => {
  const full = `${deltaEvent("split")}\n\ndata: [DONE]\n\n`;
  const mid = Math.floor(full.length / 2);
  const result = await consumeSSEStream(sseResponse([full.slice(0, mid), full.slice(mid)]));
  assert.equal(result.aggregatedContent, "split");
  assert.deepEqual(result.events.map((e) => e.type), ["data", "done"]);
});

test("non-JSON data payload becomes a raw event, not a crash", async () => {
  const result = await consumeSSEStream(sseResponse(["data: not-json\n\n"]));
  assert.deepEqual(result.events, [{ type: "raw", payload: "not-json" }]);
  assert.equal(result.aggregatedContent, "");
});

test("non-data lines (comments / other fields) are ignored", async () => {
  const result = await consumeSSEStream(
    sseResponse([`: keep-alive\n\nevent: ping\n\n${deltaEvent("ok")}\n\n`]),
  );
  assert.equal(result.aggregatedContent, "ok");
  assert.equal(result.events.length, 1);
});

// ---------------------------------------------------------------------------
// consumeSSEStream — final-buffer flush (M-30): the last SSE event without a
// trailing \n\n must still be delivered.
// ---------------------------------------------------------------------------

test("final delta without trailing \\n\\n is flushed", async () => {
  const result = await consumeSSEStream(
    sseResponse([`${deltaEvent("first")}\n\n`, deltaEvent(" last")]),
  );
  assert.equal(result.aggregatedContent, "first last");
  assert.equal(result.events.length, 2);
});

test("terminal error frame without trailing \\n\\n survives", async () => {
  const errFrame = `data: ${JSON.stringify({ error: { type: "output_blocked", message: "blocked" } })}`;
  const result = await consumeSSEStream(
    sseResponse([`${deltaEvent("partial")}\n\n`, errFrame]),
  );
  assert.equal(result.aggregatedContent, "partial");
  assert.deepEqual(result.terminalError, { type: "output_blocked", message: "blocked" });
});

test("[DONE] without trailing \\n\\n is flushed as a done event", async () => {
  const result = await consumeSSEStream(
    sseResponse([`${deltaEvent("x")}\n\ndata: [DONE]`]),
  );
  assert.equal(result.aggregatedContent, "x");
  assert.deepEqual(result.events.at(-1), { type: "done" });
});

test("multi-byte character split across chunks is decoded by the flush drain", async () => {
  // "é" (U+00E9) is two UTF-8 bytes; split them across reads in the FINAL
  // unterminated event so the trailing decoder.decode() drain must finish it.
  const bytes = encoder.encode(deltaEvent("café"));
  const result = await consumeSSEStream(
    sseResponse([bytes.slice(0, bytes.length - 1), bytes.slice(bytes.length - 1)]),
  );
  assert.equal(result.aggregatedContent, "café");
});

test("missing body reader yields an empty stream result", async () => {
  const result = await consumeSSEStream({
    headers: new Headers({ "content-type": "text/event-stream" }),
    body: null,
  });
  assert.deepEqual(result, {
    isStream: true,
    events: [],
    aggregatedContent: "",
    terminalError: null,
    raw: "",
  });
});

test("non-SSE content type takes the JSON (isStream:false) path", async () => {
  const payload = { error: { code: "content_blocked" } };
  const result = await consumeSSEStream({
    headers: new Headers({ "content-type": "application/json" }),
    text: async () => JSON.stringify(payload),
  });
  assert.equal(result.isStream, false);
  assert.deepEqual(result.data, payload);
  assert.deepEqual(result.terminalError, payload.error);
});

// ---------------------------------------------------------------------------
// getHeaderValue (M-31) via extractRoutingFromHeaders — case-insensitive reads
// on both real Headers instances and plain-object fallbacks.
// ---------------------------------------------------------------------------

const EXPECTED_ROUTING = {
  requested_model: "gpt-4o",
  original_model: "gpt-4o",
  selected_model: "zeroshield-guard-120b",
  routed_model: "zeroshield-guard-120b",
  routing_reason: "kill_switch reroute",
  decision_source: "kill_switch",
  policy_summary: "policy: reroute high-risk",
  rerouted: true,
};

test("extractRoutingFromHeaders reads a real Headers instance (any casing)", () => {
  const headers = new Headers({
    // Headers normalizes names to lowercase internally; .get() is CI.
    "X-ZEROSHIELD-ORIGINAL-MODEL": "gpt-4o",
    "x-zeroshield-routed-model": "zeroshield-guard-120b",
    "X-ZeroShield-Routing-Reason": "kill_switch reroute",
    "X-ZeroShield-Routing-Source": "kill_switch",
    "X-ZeroShield-Routing-Policy-Summary": "policy: reroute high-risk",
    "X-ZeroShield-Rerouted": "true",
  });
  assert.deepEqual(extractRoutingFromHeaders(headers), EXPECTED_ROUTING);
});

test("extractRoutingFromHeaders reads a plain object with exact-case keys", () => {
  const headers = {
    "X-ZeroShield-Original-Model": "gpt-4o",
    "X-ZeroShield-Routed-Model": "zeroshield-guard-120b",
    "X-ZeroShield-Routing-Reason": "kill_switch reroute",
    "X-ZeroShield-Routing-Source": "kill_switch",
    "X-ZeroShield-Routing-Policy-Summary": "policy: reroute high-risk",
    "X-ZeroShield-Rerouted": "true",
  };
  assert.deepEqual(extractRoutingFromHeaders(headers), EXPECTED_ROUTING);
});

test("extractRoutingFromHeaders reads a plain object with lowercase keys", () => {
  const headers = {
    "x-zeroshield-original-model": "gpt-4o",
    "x-zeroshield-routed-model": "zeroshield-guard-120b",
    "x-zeroshield-routing-reason": "kill_switch reroute",
    "x-zeroshield-routing-source": "kill_switch",
    "x-zeroshield-routing-policy-summary": "policy: reroute high-risk",
    "x-zeroshield-rerouted": "true",
  };
  assert.deepEqual(extractRoutingFromHeaders(headers), EXPECTED_ROUTING);
});

test("extractRoutingFromHeaders falls back to a CI key scan for odd casings", () => {
  const headers = {
    "X-ZEROSHIELD-Original-MODEL": "gpt-4o",
    "x-ZeroShield-routed-MODEL": "zeroshield-guard-120b",
    "X-zeroshield-Routing-reason": "kill_switch reroute",
    "X-ZEROSHIELD-ROUTING-SOURCE": "kill_switch",
    "x-zeroShield-routing-policy-summary": "policy: reroute high-risk",
    "X-ZeroShield-REROUTED": "true",
  };
  assert.deepEqual(extractRoutingFromHeaders(headers), EXPECTED_ROUTING);
});

test("extractRoutingFromHeaders handles null/missing headers gracefully", () => {
  assert.deepEqual(extractRoutingFromHeaders(null), {});
  const sparse = extractRoutingFromHeaders({});
  assert.equal(sparse.selected_model, "");
  assert.equal(sparse.rerouted, false);
});
