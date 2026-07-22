import assert from "node:assert/strict";
import test from "node:test";

import {
  isOutputGovernanceEvent,
  isRedactNoop,
  selectOutputGovernanceEvents,
} from "./outputGovernanceFeed.js";

test("selectOutputGovernanceEvents prefers output_guard over request", () => {
  const results = [
    {
      id: 1,
      timestamp: "2026-07-07T10:00:00Z",
      action: "redact",
      metadata: {
        event_type: "request",
        request_id: "zs-b037838c19b1",
        pipeline_trace: {
          stages: [{ name: "output_guardrail", action: "redact" }],
        },
      },
    },
    {
      id: 2,
      timestamp: "2026-07-07T10:00:01Z",
      action: "redact",
      metadata: {
        event_type: "output_guard",
        request_id: "zs-b037838c19b1",
        raw_output: "User Safety: unsafe",
      },
    },
  ];
  const picked = selectOutputGovernanceEvents(results);
  assert.equal(picked.length, 1);
  assert.equal(picked[0].metadata.event_type, "output_guard");
});

test("isOutputGovernanceEvent accepts pipeline output_guardrail stage", () => {
  const ev = {
    metadata: {
      event_type: "request",
      pipeline_trace: { stages: [{ name: "output_guardrail", action: "redact" }] },
    },
  };
  assert.equal(isOutputGovernanceEvent(ev), true);
});

test("isRedactNoop detects identical raw and sanitized output", () => {
  const ev = {
    action: "redact",
    metadata: {
      raw_output: "hello",
      sanitized_output: "hello",
    },
  };
  assert.equal(isRedactNoop(ev), true);
});
