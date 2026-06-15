import test from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULT_VECTOR_PROVIDER,
  VECTOR_PROVIDERS,
  VECTOR_PROVIDER_CONFIG_FIELDS,
} from "./vectorProviders.js";

test("DEFAULT_VECTOR_PROVIDER is pinecone (BYOK default)", () => {
  assert.equal(DEFAULT_VECTOR_PROVIDER, "pinecone");
});

test("VECTOR_PROVIDERS includes chroma (now BYOK) and matches backend choices", () => {
  const values = VECTOR_PROVIDERS.map((p) => p.value);
  assert.deepEqual(values, ["pinecone", "milvus", "chroma", "custom"]);
  assert.ok(values.includes("chroma"));
});

test("VECTOR_PROVIDER_CONFIG_FIELDS has entries for each provider", () => {
  for (const { value } of VECTOR_PROVIDERS) {
    assert.ok(Array.isArray(VECTOR_PROVIDER_CONFIG_FIELDS[value]));
    assert.ok(VECTOR_PROVIDER_CONFIG_FIELDS[value].length > 0);
  }
});
