#!/usr/bin/env node
/**
 * GW34 L34-1 — the stock Node `openai` SDK against the genuine gateway build_app (scripted).
 * No patched client. Prints one JSON line per cell: {"cell": "a", "ok": true|false, "detail": ...}.
 * Env: AMF_BASE_URL (no /v1), AMF_API_KEY.
 */
import OpenAI from "openai";

const base = (process.env.AMF_BASE_URL || "").replace(/\/$/, "");
const apiKey = process.env.AMF_API_KEY || "sk-gw34-matrix";
if (!base) {
  console.error("AMF_BASE_URL required");
  process.exit(2);
}
const MODEL = "gpt-4o-mini";
const marker = (name) => [{ role: "user", content: `SDKMATRIX:${name}` }];
const client = new OpenAI({ apiKey, baseURL: `${base}/v1`, maxRetries: 0 });
const TOOL = {
  type: "function",
  function: { name: "get_weather", parameters: { type: "object", properties: { city: { type: "string" } } } },
};

const results = [];
async function cell(name, fn) {
  try {
    await fn();
    results.push({ cell: name, ok: true });
  } catch (error) {
    results.push({ cell: name, ok: false, detail: String(error && error.stack || error) });
  }
  console.log(JSON.stringify(results[results.length - 1]));
}
function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}
// R1.6 (GW34 V13): the `amf.trace` SSE comment is skipped by the SDK's own event source, so
// every yielded chunk must carry real content; a non-content chunk reaching here is a regression.
const contentChunks = (chunks) => {
  for (const c of chunks) {
    assert(c && Array.isArray(c.choices) && c.choices.length, `amf.trace (or another non-content frame) reached the SDK iterator: ${JSON.stringify(c)}`);
  }
  return chunks;
};

await cell("a", async () => {
  const r = await client.chat.completions.create({ model: MODEL, messages: marker("plain") });
  assert(r.object === "chat.completion", "object");
  assert(r.choices[0].message.content === "Hello from the gateway.", "content");
  assert(r.choices[0].finish_reason === "stop", "finish_reason");
});

await cell("b", async () => {
  const stream = await client.chat.completions.create({ model: MODEL, messages: marker("plain"), stream: true });
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  const content = contentChunks(chunks);
  assert(content.length > 0, "no chunks");
  assert(content[0].choices[0].delta.role === "assistant", "role first");
  const text = content.map((c) => c.choices[0].delta.content || "").join("");
  assert(text === "Hello from the gateway.", `text ${text}`);
  assert(content[content.length - 1].choices[0].finish_reason === "stop", "finish");
  assert(new Set(content.map((c) => c.id)).size === 1, "stable id");
});

await cell("c", async () => {
  // GW34 V13 (owner decision, 4 Nov 2026): `amf.trace` is an SSE comment line, so the
  // `.stream()` helper's own event source skips it instead of throwing "chunk.choices is
  // not iterable" on the named `event: amf.trace` frame it used to receive.
  const stream = client.beta.chat.completions.stream({ model: MODEL, messages: marker("tool"), tools: [TOOL] });
  const final = await stream.finalChatCompletion();
  const call = final.choices[0].message.tool_calls[0];
  assert(call.id === "call_abc" && call.function.name === "get_weather", "call identity");
  assert(JSON.parse(call.function.arguments).city === "Paris", "arguments");
  assert(final.choices[0].finish_reason === "tool_calls", "finish_reason");
});

await cell("d", async () => {
  const stream = await client.chat.completions.create({
    model: MODEL, messages: marker("two_tools"), tools: [TOOL], stream: true });
  const calls = {};
  for await (const chunk of stream) {
    if (!chunk.choices) continue;
    for (const fragment of chunk.choices[0]?.delta?.tool_calls || []) {
      const e = (calls[fragment.index] ||= { id: "", name: "", arguments: "" });
      e.id += fragment.id || "";
      e.name += fragment.function?.name || "";
      e.arguments += fragment.function?.arguments || "";
    }
  }
  assert(JSON.stringify(Object.keys(calls)) === JSON.stringify(["0", "1"]), "dense indexes");
  assert(calls[0].arguments === '{"city":"Paris"}' && calls[1].arguments === '{"city":"Rome"}', "args");
});

await cell("e", async () => {
  const r = await client.chat.completions.create({ model: MODEL, messages: marker("tool"), tools: [TOOL] });
  const call = r.choices[0].message.tool_calls[0];
  assert(call.id === "call_abc" && call.type === "function" && call.function.name === "get_weather", "call");
  assert(JSON.parse(call.function.arguments).unit === "c", "arguments");
});

await cell("f", async () => {
  const stream = await client.chat.completions.create({ model: MODEL, messages: marker("slow"), stream: true });
  let n = 0;
  for await (const _chunk of stream) {
    n += 1;
    stream.controller.abort();
    break;
  }
  assert(n === 1, "first chunk then abort");
  // a second request after the abort still succeeds
  const r = await client.chat.completions.create({ model: MODEL, messages: marker("plain") });
  assert(r.choices[0].message.content === "Hello from the gateway.", "second request");
});

await cell("g", async () => {
  const r = await client.embeddings.create({ model: MODEL, input: "one string" });
  assert(r.object === "list" && r.data.length === 1 && Array.isArray(r.data[0].embedding), "embeddings");
});

await cell("h", async () => {
  const r = await client.responses.create({ model: MODEL, input: "SDKMATRIX:plain" });
  assert(r.object === "response" && r.status === "completed", "responses.create");
  const events = [];
  const s = await client.responses.create({ model: MODEL, input: "SDKMATRIX:plain", stream: true });
  for await (const event of s) events.push(event);
  assert(events.length && events[events.length - 1].type === "response.completed", "responses stream");
});

await cell("i", async () => {
  const bad = new OpenAI({ apiKey: "sk-wrong", baseURL: `${base}/v1`, maxRetries: 0 });
  let ok = false;
  try { await bad.chat.completions.create({ model: MODEL, messages: marker("plain") }); }
  catch (e) { ok = e instanceof OpenAI.AuthenticationError; }
  assert(ok, "AuthenticationError");
  ok = false;
  try {
    await client.chat.completions.create({ model: MODEL, messages: [{ role: "user", content: "key AKIAIOSFODNN7EXAMPLE" }] });
  } catch (e) { ok = e instanceof OpenAI.BadRequestError && e.code === "input_blocked"; }
  assert(ok, "BadRequestError input_blocked");
  ok = false;
  try { await client.files.list(); } catch (e) { ok = e instanceof OpenAI.NotFoundError; }
  assert(ok, "NotFoundError for /v1/files");
});

await cell("j", async () => {
  let status = 0;
  try {
    await client.chat.completions.create({ model: MODEL, messages: marker("blocked_output"), stream: true });
  } catch (e) { status = e.status; }
  assert(status === 403, `blocked_output status ${status}`);
  status = 0;
  try {
    await client.chat.completions.create({ model: MODEL, messages: marker("upstream_timeout"), stream: true });
  } catch (e) { status = e.status; }
  assert(status === 504, `upstream_timeout status ${status}`);
});

process.exit(results.every((r) => r.ok) ? 0 : 1);
