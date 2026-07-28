import test from "node:test";
import assert from "node:assert/strict";
import {
  buildWebSocketUrl,
  eventBelongsToOrg,
} from "./realtimeNotificationsScope.js";

test("buildWebSocketUrl includes organization_id when provided", () => {
  const url = buildWebSocketUrl("tok-abc", 42);
  assert.match(url, /\/ws\/notifications\/\?/);
  assert.match(url, /token=tok-abc/);
  assert.match(url, /organization_id=42/);
});

test("buildWebSocketUrl omits organization_id when missing", () => {
  const url = buildWebSocketUrl("tok-abc", null);
  assert.match(url, /token=tok-abc/);
  assert.equal(url.includes("organization_id="), false);
});

test("eventBelongsToOrg allows matching organization_id", () => {
  assert.equal(eventBelongsToOrg({ organization_id: 7, type: "enforcement_event" }, 7), true);
});

test("eventBelongsToOrg drops foreign organization_id", () => {
  assert.equal(eventBelongsToOrg({ organization_id: 9, type: "enforcement_event" }, 7), false);
});

test("eventBelongsToOrg reads nested metadata.organization_id", () => {
  assert.equal(
    eventBelongsToOrg({ metadata: { organization_id: "9" }, type: "enforcement_event" }, 7),
    false,
  );
  assert.equal(
    eventBelongsToOrg({ metadata: { organization_id: "7" }, type: "enforcement_event" }, 7),
    true,
  );
});

test("eventBelongsToOrg allows legacy payloads without organization_id", () => {
  assert.equal(eventBelongsToOrg({ type: "enforcement_event", action: "block" }, 7), true);
});
