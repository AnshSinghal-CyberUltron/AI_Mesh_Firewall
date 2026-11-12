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

// Added 12 Nov for direction 4 (Lattice): the screens no earlier direction drew
// (request detail, gateway and API keys) and the extra overview boxes. New keys
// only; nothing above changes, so the first three directions render as before.
// Field names follow the console inventory (docs/plans/evidence/2026-10-26-gw14b)
// and today's LogDetailPage / GatewayKeyPanel.
(function () {
  const S = window.SAMPLE;
  S.asOf = "11 Nov 2026, 12:00 IST";
  S.fleet = { service: "gateway-v2", replicas: 3, serving: 3, plan: 14, syncAgo: "4 s", configKeys: 66 };
  S.overhead = { p50: 6.8, p95: 11.9, p99: 16.4, budget: 20,
    hist: [2, 9, 26, 41, 52, 44, 31, 22, 14, 9, 6, 4, 3, 2, 1, 1, 0, 0, 0, 0] };
  S.detectors = { tier1Presets: 42, tier1Regex: 7, tier2Model: "guard model on Bedrock", tier2P95: 38, failClosed: true, unavailable: 0 };
  S.models = [
    { name: "gpt-4o", provider: "OpenAI", state: "live", share: 21 },
    { name: "gpt-4o-mini", provider: "OpenAI", state: "isolated", share: 0 },
    { name: "llama-3.1-70b", provider: "Bedrock", state: "live", share: 64 },
    { name: "claude-3.5-sonnet", provider: "Bedrock", state: "live", share: 9 },
    { name: "mistral-large", provider: "Mistral", state: "live", share: 6 },
    { name: "text-embedding-3-small", provider: "OpenAI", state: "live", share: 0 },
  ];
  S.mcp = { servers: 5, reachable: 4, tools: 61, denied: 14, unreachable: "docs-search", since: "6 min" };
  S.rag = { stores: [
    { name: "support-kb", withheld: 96, chunks: "41,200" },
    { name: "policies-kb", withheld: 0, chunks: "3,180" },
    { name: "product-docs", withheld: 0, chunks: "12,904" },
  ] };
  S.enforcementLog = { written: 18204, lastAgo: "3 s", dropped: 0 };
  S.keys = {
    total: 15, active: 12, revoked: 3, idle: 6, expiringSoon: 2, authFailures: 41, invalid: 33, expired: 8,
    baseUrl: "https://gateway.example.internal/v1",
    rows: [
      { name: "Production support bot", prefix: "zs-prod-7f3", project: "Support", models: "gpt-4o-mini, llama-3.1-70b", tpm: "60,000", risk: 0.42, used: "12 s ago", expires: "30 Jan 2027", status: "active", requests: 11286 },
      { name: "Sales assistant", prefix: "zs-prod-a91", project: "Sales", models: "gpt-4o", tpm: "40,000", risk: 0.18, used: "1 min ago", expires: "14 Mar 2027", status: "active", requests: 3904 },
      { name: "Internal RAG search", prefix: "zs-prod-c22", project: "Knowledge", models: "llama-3.1-70b", tpm: "20,000", risk: 0.1, used: "3 min ago", expires: "No expiry", status: "active", requests: 1722 },
      { name: "Build tools agent", prefix: "zs-prod-e5d", project: "Platform", models: "claude-3.5-sonnet", tpm: "30,000", risk: 0.65, used: "6 min ago", expires: "18 Nov 2026", status: "expiring", requests: 871 },
      { name: "Staging smoke tests", prefix: "zs-stg-41b", project: "QA", models: "All models", tpm: "10,000", risk: 0.05, used: "2 h ago", expires: "15 Nov 2026", status: "expiring", requests: 412 },
      { name: "Analytics batch", prefix: "zs-prod-90f", project: "Data", models: "gpt-4o-mini", tpm: "15,000", risk: 0.22, used: "9 h ago", expires: "No expiry", status: "active", requests: 9 },
      { name: "Old demo key", prefix: "zs-dev-3aa", project: "Demo", models: "All models", tpm: "5,000", risk: null, used: "41 days ago", expires: "Revoked 2 Oct", status: "revoked", requests: 0 },
      { name: "Contractor sandbox", prefix: "zs-dev-7c0", project: "Demo", models: "gpt-4o-mini", tpm: "5,000", risk: null, used: "63 days ago", expires: "Revoked 9 Sep", status: "revoked", requests: 0 },
    ],
  };
  S.request = {
    id: "7c1e-42ab-9d10-5be2", short: "7c1e-42ab", eventId: 48211, incident: null, plan: 14,
    time: "11 Nov 2026, 11:42:08 IST", method: "POST", endpoint: "/v1/chat/completions",
    sourceIp: "10.20.4.17", userAgent: "openai-python/1.54.3", key: "zs-prod-7f3", keyName: "Production support bot",
    project: "Support", agent: "support-bot", requested: "gpt-4o-mini", routed: "llama-3.1-70b",
    routeReason: "gpt-4o-mini isolated by the kill switch since 09:14", action: "redact", status: 200,
    totalMs: 742.0, overheadMs: 11.7, ttftMs: 318, tokensIn: 182, tokensOut: 214, cost: "0.0009",
    risk: 0.62, threat: "Medium", owasp: "LLM02", tags: ["DPDP", "GDPR"],
    stages: [
      { name: "Auth", action: "allow", ms: 0.4, detail: "Key zs-prod-7f3… valid for Default organization" },
      { name: "Rate Limit", action: "allow", ms: 0.2, detail: "2,140 of 60,000 tokens per minute used" },
      { name: "Policy Engine", action: "redact", ms: 1.9, detail: "PII_REDACT_OUT rule 1 (email) and rule 2 (phone)" },
      { name: "Input Scan (Tier-1/2)", action: "allow", ms: 6.1, detail: "No injection or jailbreak signal; Tier-2 guard 5.2 ms" },
      { name: "Kill-Switch", action: "reroute", ms: 0.1, detail: "gpt-4o-mini isolated since 09:14" },
      { name: "Model Routing", action: "reroute", ms: 0.6, detail: "Sent to llama-3.1-70b, the policy fallback" },
      { name: "Model Call", action: "upstream", ms: 730.3, detail: "llama-3.1-70b on Bedrock; not firewall time" },
      { name: "Output Guard", action: "allow", ms: 2.4, detail: "No secrets or personal data in the response" },
    ],
    inputBefore: "Draft a reply to Priya Sharma (priya.sharma@example.com, +91 98765 43210) about invoice 4471 and copy her manager.",
    output: "Hi Priya, thanks for flagging invoice 4471. I have copied your manager, and we will confirm the corrected amount by Friday.",
    checks: [
      ["Input validation", "allow", "Passed"], ["PII detection", "redact", "2 found, replaced"], ["Prompt injection", "allow", "None"],
      ["Jailbreak attempt", "allow", "None"], ["Content safety", "allow", "Passed"], ["Rate limit", "allow", "Within limit"],
      ["Auth", "allow", "Valid key"], ["Policy violations", "redact", "1 policy, 2 rules"],
    ],
  };
})();
