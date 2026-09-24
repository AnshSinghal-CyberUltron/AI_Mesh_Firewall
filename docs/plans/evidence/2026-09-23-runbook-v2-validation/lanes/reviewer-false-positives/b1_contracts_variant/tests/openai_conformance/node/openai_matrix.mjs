#!/usr/bin/env node
/**
 * LGW01-3 — stock Node openai SDK against a wire (stub TCP or live :8300).
 * No custom parser. Fail if any assertion needs a patched client.
 */
import OpenAI, { APIError, AuthenticationError } from "openai";

const base = (process.env.AMF_CONFORMANCE_BASE_URL || "").replace(/\/$/, "");
if (!base) {
  console.error("AMF_CONFORMANCE_BASE_URL required");
  process.exit(2);
}
const apiKey = process.env.AMF_CONFORMANCE_API_KEY || "zs_test_sdk_compat_0123456789abcdef";
const live = process.env.AMF_CONFORMANCE_LIVE === "1";

function fail(msg) {
  console.error("FAIL", msg);
  process.exit(1);
}

async function main() {
  const client = new OpenAI({ apiKey, baseURL: `${base}/v1`, maxRetries: 0 });
  const bad = new OpenAI({
    apiKey: "sk-bogus",
    baseURL: `${base}/v1`,
    maxRetries: 0,
  });

  try {
    await bad.chat.completions.create({
      model: "gpt-4o-mini",
      messages: [{ role: "user", content: "hi" }],
    });
    fail("bogus key did not raise");
  } catch (err) {
    if (!(err instanceof AuthenticationError) && !(err instanceof APIError && err.status === 401)) {
      fail(`typed auth error missing: ${err}`);
    }
  }

  if (live) {
    console.log("lgw01_3_live_auth_ok", base);
    return;
  }

  const completion = await client.chat.completions.create({
    model: "gpt-4o-mini",
    messages: [{ role: "user", content: "Say hello politely." }],
  });
  if (completion.choices[0].message.content !== "Hello from upstream.") {
    fail(`unexpected content ${completion.choices[0].message.content}`);
  }

  const stream = await client.chat.completions.create({
    model: "gpt-4o-mini",
    messages: [{ role: "user", content: "stream" }],
    stream: true,
  });
  let text = "";
  for await (const chunk of stream) {
    text += chunk.choices[0]?.delta?.content || "";
  }
  if (text !== "Hello streaming world.") {
    fail(`stream content ${text}`);
  }

  const tools = await client.chat.completions.create({
    model: "gpt-4o-mini",
    messages: [{ role: "user", content: "weather?" }],
    tools: [
      {
        type: "function",
        function: { name: "get_weather", parameters: { type: "object", properties: {} } },
      },
    ],
  });
  const call = tools.choices[0].message.tool_calls?.[0];
  if (!call || call.function.name !== "get_weather") {
    fail("tool call not reconstructed");
  }

  console.log("lgw01_3_node_ok", base);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
