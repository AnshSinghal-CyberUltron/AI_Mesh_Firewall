// Illustrative sample data shared by all three directions so the owner compares
// visual systems, not content. Field names mirror the real control API
// (soc-kpis, module-kpis, policies, policies/{id}/rules, owasp-stats).
// Every mockup that shows these numbers carries a visible "Sample data" marker.
(function () {
  const totals = { requests: 18204, allowed: 16231, redacted: 1377, monitored: 184, blocked: 412, critical: 9 };

  // 24 hourly buckets whose sums equal the totals above (deterministic shape).
  const shape = [3, 2, 2, 1, 1, 2, 4, 7, 10, 12, 11, 10, 9, 10, 11, 12, 11, 9, 7, 6, 5, 4, 4, 3];
  const sum = shape.reduce((a, b) => a + b, 0);
  const spread = (total, wobble) => {
    const raw = shape.map((s, i) => Math.round((total * s) / sum * (1 + wobble * Math.sin(i * 1.7))));
    raw[15] += total - raw.reduce((a, b) => a + b, 0);
    return raw;
  };
  const allow = spread(totals.allowed, 0.04);
  const redact = spread(totals.redacted, 0.18);
  const monitor = spread(totals.monitored, 0.3);
  const block = spread(totals.blocked, 0.45);
  const hourly = shape.map((_, i) => ({ h: i, allow: allow[i], redact: redact[i], monitor: monitor[i], block: block[i] }));

  window.SAMPLE = {
    org: "Default organization",
    user: "Asha Rao",
    period: "Last 24 hours",
    totals,
    blockRate: ((totals.blocked / totals.requests) * 100).toFixed(1),
    redactRate: ((totals.redacted / totals.requests) * 100).toFixed(1),
    hourly,
    checkpoints: [
      { id: "1.1", name: "Gateway ingress", short: "Ingress", metric: "18,204", unit: "requests", sub: "12 active keys", tone: "neutral" },
      { id: "1.2", name: "Policy enforcement", short: "Policy", metric: "412", unit: "blocked", sub: "1,377 redacted", tone: "block" },
      { id: "1.3", name: "RAG and vector stores", short: "RAG", metric: "96", unit: "chunks denied", sub: "3 stores", tone: "monitor" },
      { id: "1.4", name: "MCP tools", short: "MCP", metric: "14", unit: "calls denied", sub: "5 servers", tone: "block" },
      { id: "1.5", name: "Model routing", short: "Routing", metric: "31", unit: "reroutes", sub: "6 models", tone: "neutral" },
      { id: "1.6", name: "Kill switch", short: "Isolation", metric: "1", unit: "model isolated", sub: "since 09:14", tone: "monitor" },
      { id: "1.7", name: "Output guardrails", short: "Output", metric: "57", unit: "redacted", sub: "2 blocked", tone: "redact" },
    ],
    attention: [
      { tone: "block", label: "Blocked", title: "Prompt injection blocked 38 times", meta: ["Key zs-prod-7f3…", "Policy INJECTION_BLOCK"], when: "11:42" },
      { tone: "monitor", label: "Isolated", title: "gpt-4o-mini isolated by kill switch", meta: ["Engaged by Asha Rao", "Traffic rerouted to llama-3.1-70b"], when: "09:14" },
      { tone: "block", label: "Denied", title: "MCP tool shell_exec denied 14 times", meta: ["Server build-tools", "Policy MCP_WRITE_GUARD"], when: "08:57" },
      { tone: "redact", label: "Withheld", title: "96 retrieved chunks withheld", meta: ["Store support-kb", "Policy RAG_CUSTOMER_DATA"], when: "07:30" },
    ],
    topRules: [
      { rule: "Email addresses", policy: "PII_REDACT_OUT", action: "redact", hits: 904 },
      { rule: "Phone numbers", policy: "PII_REDACT_OUT", action: "redact", hits: 377 },
      { rule: "Instruction override phrases", policy: "INJECTION_BLOCK", action: "block", hits: 318 },
      { rule: "Source code blocks", policy: "CODE_EXFIL_WATCH", action: "monitor", hits: 184 },
      { rule: "API keys and tokens", policy: "PII_REDACT_OUT", action: "block", hits: 61 },
    ],
    owasp: [
      { id: "LLM01", name: "Prompt injection", hits: 318 },
      { id: "LLM02", name: "Sensitive information disclosure", hits: 1102 },
      { id: "LLM05", name: "Improper output handling", hits: 41 },
      { id: "LLM06", name: "Excessive agency", hits: 14 },
      { id: "LLM07", name: "System prompt leakage", hits: 22 },
      { id: "LLM08", name: "Vector and embedding weaknesses", hits: 96 },
    ],
    policies: {
      pipeline: [
        { name: "Prompt injection guard", code: "INJECTION_BLOCK", severity: "critical", priority: 5, enabled: true, rules: 3 },
        { name: "PII redaction (outbound)", code: "PII_REDACT_OUT", severity: "high", priority: 10, enabled: true, rules: 4, selected: true },
        { name: "Source code exfiltration", code: "CODE_EXFIL_WATCH", severity: "medium", priority: 20, enabled: true, rules: 2 },
        { name: "Profanity in support replies", code: "TONE_SUPPORT", severity: "low", priority: 40, enabled: false, rules: 1 },
      ],
      rag: [{ name: "Customer data in RAG answers", code: "RAG_CUSTOMER_DATA", severity: "high", priority: 10, enabled: true, rules: 2 }],
      mcp: [{ name: "Write tools on MCP servers", code: "MCP_WRITE_GUARD", severity: "critical", priority: 5, enabled: true, rules: 2 }],
    },
    editor: {
      name: "PII redaction (outbound)", code: "PII_REDACT_OUT", category: "Data protection", severity: "high", priority: 10,
      description: "Replace personal data with typed placeholders before a prompt leaves the network. Block outright if a credential is present.",
      scope: { users: "", agents: "support-bot, sales-assistant", roles: "" },
      rules: [
        { name: "Email addresses", type: "preset", pattern: "email", action: "redact", stage: "Input", hits: 904 },
        { name: "Phone numbers", type: "preset", pattern: "phone (IN, US)", action: "redact", stage: "Input", hits: 377 },
        { name: "National ID numbers", type: "regex", pattern: "\\b\\d{4}\\s?\\d{4}\\s?\\d{4}\\b", action: "redact", stage: "Input", hits: 12 },
        { name: "API keys and tokens", type: "preset", pattern: "secret", action: "block", stage: "Input and output", hits: 61 },
      ],
      compiled: { version: 14, ago: "2 minutes ago", pending: 1 },
      test: {
        input: "Draft a reply to Priya Sharma (priya.sharma@example.com, +91 98765 43210) about invoice 4471 and copy her manager.",
        segments: [
          ["Draft a reply to Priya Sharma (", null],
          ["priya.sharma@example.com", "EMAIL_1"],
          [", ", null],
          ["+91 98765 43210", "PHONE_1"],
          [") about invoice 4471 and copy her manager.", null],
        ],
        verdict: "redact",
        fired: ["Email addresses", "Phone numbers"],
      },
    },
  };
})();
