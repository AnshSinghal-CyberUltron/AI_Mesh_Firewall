// Which reachable frontend files consume gateway-produced data, and which gateway-produced
// field names they read. Inputs: fe_graph.json (SWC graph), gw_keys.json (Python AST key universes).
const fs = require("fs");
const g = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const k = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const READ_ENDPOINTS = ["/api/security/threat-feed", "/api/security/soc-kpis", "/api/security/module-kpis", "/api/security/module-trends",
  "/api/security/attack-vector-trends", "/api/security/module-charts", "/api/security/owasp-stats", "/api/security/rag-pipeline-kpis",
  "/api/security/incidents", "/api/gateways/stats", "/api/policies/analytics", "/api/policies/top-violators", "/api/policy/top-rules",
  "/api/models/status", "/api/models/audit-log", "/api/mcp-connector/events", "/api/admin/gateway/circuit-breaker/state",
  "/api/admin/gateway/rag/collections", "/api/notifications", "/api/health/services", "/api/mcp-connector/tools/call",
  "/v1/chat/completions", "/v1/rag/query", "/v1/rag/ingest", "/v1/models", "/v1/admin/logs"];
const PAYLOAD_IDS = ["pipeline_trace", "stage_metrics", "stage_metrics_ms", "latency_breakdown", "guard_summary", "final_action",
  "security_risk_score", "event_type", "matched_policies", "owasp_code", "overhead_ms", "zeroshield", "threat_category"];
const G = new Set(k.G_gateway), GT = new Set(k.G_trace), GTel = new Set(k.G_telemetry), CEE = new Set(k.C_ee), CF = new Set(k.C_feed);
const consumers = [];
for (const [f, v] of Object.entries(g.files)) {
  if (!v.strings) continue;
  const eps = READ_ENDPOINTS.filter((e) => v.strings.some((s) => s.includes(e)));
  const ids = PAYLOAD_IDS.filter((p) => v.reads.includes(p) || v.strings.includes(p));
  if (eps.length || ids.length) consumers.push({ f, eps, ids, reads: v.reads, bytes: v.bytes });
}
const F = new Set(consumers.flatMap((c) => c.reads));
const inter = (A, B) => [...A].filter((x) => B.has(x)).sort();
const any = new Set([...G, ...CEE, ...CF]);
const res = {
  consumer_files: consumers.map((c) => ({ file: c.f, bytes: c.bytes, endpoints: c.eps, payload_ids: c.ids })),
  F_size: F.size,
  F_and_G_gateway: inter(F, G),
  F_and_G_trace: inter(F, GT),
  F_and_G_telemetry: inter(F, GTel),
  F_and_C_ee: inter(F, CEE),
  F_and_C_ee_not_G: inter(F, CEE).filter((x) => !G.has(x)),
  F_and_any: inter(F, any),
};
fs.writeFileSync(process.argv[4], JSON.stringify(res, null, 1));
console.log(`consumer files (reachable): ${consumers.length}  bytes=${consumers.reduce((a, c) => a + c.bytes, 0)}`);
for (const c of consumers) console.log(`  ${String(c.bytes).padStart(6)} ${c.f}  eps=[${c.eps.join(",")}] ids=[${c.ids.join(",")}]`);
console.log(`distinct property names read in consumer files: ${F.size}`);
for (const [name, arr] of Object.entries(res)) if (Array.isArray(arr) && name.startsWith("F_")) console.log(`${name}: ${arr.length}`);
