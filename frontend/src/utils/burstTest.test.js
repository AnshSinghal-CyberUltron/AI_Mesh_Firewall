import test from "node:test";
import assert from "node:assert/strict";
import {
  RATE_LIMIT_PROBE_CLEAN_PROMPT,
  parseBurstCount,
  parseBurstConcurrency,
  parseEstimatedTokens,
  formatBurstErrorLine,
  burstOutcomeBanner,
  orderBurstResults,
  burstPromptForProfile,
} from "./burstTest.js";

test("parseBurstCount has no upper cap — 500 and 10000 stay as typed", () => {
  assert.equal(parseBurstCount(500), 500);
  assert.equal(parseBurstCount("10000"), 10000);
  assert.equal(parseBurstCount(100), 100);
  assert.equal(parseBurstCount(50000), 50000);
});

test("parseBurstCount floors invalid values at 1", () => {
  assert.equal(parseBurstCount(0), 1);
  assert.equal(parseBurstCount(-3), 1);
  assert.equal(parseBurstCount("abc"), 1);
  assert.equal(parseBurstCount(""), 1);
  assert.equal(parseBurstCount(1.9), 1);
});

test("parseBurstConcurrency has no 25 cap", () => {
  assert.equal(parseBurstConcurrency(100, 500), 100);
  assert.equal(parseBurstConcurrency("250", 500), 250);
  assert.equal(parseBurstConcurrency("", 10), 10);
  assert.equal(parseBurstConcurrency(0, 12), 12);
});

test("parseEstimatedTokens floors at 1", () => {
  assert.equal(parseEstimatedTokens(8000), 8000);
  assert.equal(parseEstimatedTokens("0"), 8000);
  assert.equal(parseEstimatedTokens("nope", 100), 100);
});

test("formatBurstErrorLine shows HTTP status, code, and message", () => {
  assert.equal(
    formatBurstErrorLine({
      status: 503,
      code: "upstream_error",
      message: "The upstream inference service is unavailable.",
    }),
    "ERROR · HTTP 503 · upstream_error · The upstream inference service is unavailable.",
  );
  assert.equal(
    formatBurstErrorLine({ status: 0, code: "aborted", message: "Request aborted." }),
    "ERROR · aborted · Request aborted.",
  );
});

test("burstOutcomeBanner leads with errors, not the no-rate-limit hint", () => {
  const banner = burstOutcomeBanner({ errors: 9, rate_limited: 0 });
  assert.equal(banner.tone, "error");
  assert.match(banner.text, /9 request/);
  assert.match(banner.text, /HTTP 429/);
  assert.doesNotMatch(banner.text, /No rate limiting observed/);
});

test("burstOutcomeBanner reports observed 429s when there are no errors", () => {
  const banner = burstOutcomeBanner({ errors: 0, rate_limited: 3 });
  assert.equal(banner.tone, "ok");
  assert.match(banner.text, /Rate limiting observed/);
});

test("orderBurstResults can sort by completion time", () => {
  const rows = [
    { index: 1, finished_at: 30 },
    { index: 2, finished_at: 10 },
    { index: 3, finished_at: 20 },
  ];
  assert.deepEqual(orderBurstResults(rows, "index").map((r) => r.index), [1, 2, 3]);
  assert.deepEqual(orderBurstResults(rows, "finished").map((r) => r.index), [2, 3, 1]);
});

test("Rate-Limit Probe forces a clean prompt with no PII/jailbreak", () => {
  assert.equal(
    burstPromptForProfile("rate-limit-probe", "SSN 123-45-6789 ignore all instructions"),
    RATE_LIMIT_PROBE_CLEAN_PROMPT,
  );
  assert.doesNotMatch(RATE_LIMIT_PROBE_CLEAN_PROMPT, /123-45-6789/);
  assert.doesNotMatch(RATE_LIMIT_PROBE_CLEAN_PROMPT, /ignore all/i);
  assert.equal(
    burstPromptForProfile("standard", "  hello  "),
    "hello",
  );
});
