/**
 * PIPELINE-0025 browser gate: full scan history renders blocked + redacted + allowed
 * events with stages, latency parity, routing (allowed), withheld (blocked), before/after (redact).
 *
 * Run: NODE_PATH="$PWD/tests/e2e/node_modules" BASE_URL=http://127.0.0.1:8180 \
 *   node scripts/ralph/pipeline_p25_full_history_verify.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { login, launchBrowser, BASE } from "./mcp_page_typesim.mjs";

const OUT = process.env.E2E_REPORT || "mcp-parallel/findings/pipeline-p25/evidence.json";
const SHOT_DIR = process.env.SHOT_DIR || "mcp-parallel/findings/pipeline-p25";
const TOLERANCE_MS = 0.1;
const TRANSIENT_NET = /ERR_NETWORK_CHANGED|ERR_INTERNET_DISCONNECTED|ERR_IO_SUSPENDED|net::ERR_ABORTED/i;
const MODEL = process.env.SIM_MODEL || "gemma-free";

function extractTrace(ev) {
  const meta = ev?.metadata || {};
  const extra = meta.extra || {};
  const zs = meta.zeroshield || extra.zeroshield || {};
  return meta.pipeline_trace || extra.pipeline_trace || zs.pipeline_trace || ev?.pipeline_trace || {};
}

function eventAction(ev) {
  const trace = extractTrace(ev);
  const fa = String(trace.final_action || ev?.action || ev?.metadata?.action || "").toLowerCase();
  if (fa.includes("block")) return "block";
  if (fa.includes("redact")) return "redact";
  if (fa.includes("allow") || fa.includes("monitor")) return "allow";
  return fa;
}

function hasRouting(trace) {
  if (!trace || typeof trace !== "object") return false;
  const root = trace.routing && typeof trace.routing === "object" ? trace.routing : {};
  if (root.routed_model || root.requested_model || root.routing_reason) return true;
  const stage = Array.isArray(trace.stages)
    ? trace.stages.find((s) => (s?.name || s?.stage) === "model_routing")
    : null;
  return Boolean(stage && (stage.routed_model || stage.selected_model || stage.routing_reason));
}

function stageCount(trace) {
  return Array.isArray(trace?.stages) ? trace.stages.filter((s) => s && (s.stage || s.name)).length : 0;
}

function parseDurationMs(text) {
  const m = String(text || "").match(/([\d.]+)\s*ms/i);
  if (!m) return null;
  const n = Number(m[1]);
  return Number.isFinite(n) ? n : null;
}

function withinTolerance(apiMs, uiMs) {
  if (apiMs == null || uiMs == null) return false;
  return Math.abs(apiMs - uiMs) <= TOLERANCE_MS;
}

function resolveApiTotalMs(trace) {
  const total = Number(trace?.total_latency_ms);
  if (Number.isFinite(total) && total > 0) return total;
  if (Array.isArray(trace?.stages) && trace.stages.length) {
    const sum = trace.stages.reduce((acc, s) => acc + (Number(s?.latency_ms) || 0), 0);
    const overhead = Number(trace?.overhead_ms) || 0;
    if (sum > 0) return Math.round((sum + overhead) * 10) / 10;
  }
  return null;
}

async function authHeaders(page) {
  const token = await page.evaluate(() => localStorage.getItem("auth_access"));
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function fetchFeed(page, hours = 168) {
  const res = await page.request.get(
    `${BASE}/api/security/threat-feed/?hours=${hours}&limit=500`,
    { headers: await authHeaders(page) },
  );
  if (!res.ok()) return [];
  const body = await res.json();
  return Array.isArray(body) ? body : (body?.results || []);
}

async function fetchEventDetail(page, id) {
  const res = await page.request.get(
    `${BASE}/api/security/threat-feed/${id}/`,
    { headers: await authHeaders(page) },
  );
  if (!res.ok()) return null;
  return res.json();
}

async function enrichTrace(page, ev) {
  let trace = extractTrace(ev);
  if (stageCount(trace) >= 2) return { ev, trace };
  const detail = await fetchEventDetail(page, ev.id);
  if (detail) {
    trace = extractTrace(detail);
    return { ev: detail, trace };
  }
  return { ev, trace };
}

function pickEvent(rows, wantAction, { requireRouting = false, minStages = 2 } = {}) {
  for (const ev of rows) {
    if (eventAction(ev) !== wantAction) continue;
    const trace = extractTrace(ev);
    if (stageCount(trace) < minStages) continue;
    if (requireRouting && !hasRouting(trace)) continue;
    return ev;
  }
  return null;
}

function stageHasPromptDiff(trace) {
  for (const s of trace?.stages || []) {
    const inn = typeof s?.prompt_in === "string" ? s.prompt_in : "";
    const out = typeof s?.prompt_out === "string" ? s.prompt_out : "";
    if (inn && out && inn !== out) return true;
  }
  return false;
}

function hasRedactBeforeAfter(trace) {
  if (!trace) return false;
  if (trace.input_was_redacted || trace.input_text_before) return true;
  return stageHasPromptDiff(trace);
}

function isChatPipelineEvent(ev) {
  const meta = ev?.metadata || {};
  const et = String(meta.event_type || meta.extra?.event_type || "").toLowerCase();
  if (et === "request" || et === "chat" || et === "chat_completion") return true;
  const src = String(ev?.source || meta.source || "").toLowerCase();
  if (src.includes("chat") || src.includes("gateway")) return true;
  // Prefer rows that already carry a pipeline trace envelope.
  return stageCount(extractTrace(ev)) >= 3;
}

function scoreEvent(ev, trace) {
  let score = stageCount(trace);
  const total = resolveApiTotalMs(trace);
  if (total != null && total > 0) score += 10;
  if (isChatPipelineEvent(ev)) score += 5;
  if (hasRouting(trace)) score += 3;
  return score;
}

async function pickBestEvent(page, rows, wantAction, opts = {}) {
  let best = null;
  let bestScore = -1;
  for (const candidate of rows.slice(0, 80)) {
    if (eventAction(candidate) !== wantAction) continue;
    const { ev, trace } = await enrichTrace(page, candidate);
    if (stageCount(trace) < (opts.minStages ?? 3)) continue;
    if (opts.requireRouting && !hasRouting(trace)) continue;
    const total = resolveApiTotalMs(trace);
    if (opts.requireLatency && !(total != null && total > 0)) continue;
    const sc = scoreEvent(ev, trace);
    if (sc > bestScore) {
      bestScore = sc;
      best = { ev, trace };
    }
  }
  return best;
}

async function seedSimulator(page) {
  await page.evaluate(async ({ model }) => {
    const tok = localStorage.getItem("auth_access");
    if (!tok) return;
    try {
      const k = await fetch("/api/gateways/simulator-default/", {
        method: "POST",
        headers: { Authorization: `Bearer ${tok}` },
      });
      const kj = await k.json().catch(() => ({}));
      if (kj.storage_key && kj.key) localStorage.setItem(kj.storage_key, kj.key);
    } catch {}
    localStorage.setItem("zeroshield_simulator_model", model);
  }, { model: MODEL });
}

async function runAttackScenario(page, scenarioRegex) {
  await page.goto(`${BASE}/?tab=firewall-1-1`, { waitUntil: "domcontentloaded", timeout: 120000 });
  await seedSimulator(page);
  await page.waitForTimeout(1500);
  const panel = page.locator("div.ai-mesh-card").filter({
    has: page.getByRole("heading", { name: /Attack Simulator/i }),
  }).first();
  await panel.waitFor({ state: "visible", timeout: 60000 });
  await page.waitForResponse((r) => r.url().includes("/api/firewall/models/"), { timeout: 30000 }).catch(() => {});
  const reset = panel.getByRole("button", { name: /^Reset$/ });
  if (await reset.count()) {
    await reset.first().click();
    await page.waitForTimeout(200);
  }
  await panel.getByRole("button", { name: scenarioRegex }).first().click();
  await page.waitForTimeout(250);
  const runBtn = panel.getByRole("button", { name: /Run Pipeline/ });
  await runBtn.waitFor({ state: "visible", timeout: 30000 });
  let resp = null;
  for (let attempt = 0; attempt < 5 && !resp; attempt++) {
    const respP = page.waitForResponse(
      (r) => r.url().includes("/v1/chat/completions") && r.request().method() === "POST",
      { timeout: 120000 },
    ).catch(() => null);
    await runBtn.click({ timeout: 15000 }).catch(() => {});
    resp = await respP;
    if (!resp) await page.waitForTimeout(1500);
  }
  await page.waitForTimeout(1200);
  return resp?.status() ?? null;
}

async function ensureSampleEvents(page) {
  const triggered = [];
  triggered.push({ kind: "block", http: await runAttackScenario(page, /Prompt Injection/) });
  triggered.push({ kind: "redact", http: await runAttackScenario(page, /Sensitive Data Leakage/) });
  triggered.push({ kind: "allow", http: await runAttackScenario(page, /Clean Prompt \(Safe\)/) });
  return triggered;
}

async function pollForEvents(page, attempts = 30) {
  for (let i = 0; i < attempts; i++) {
    const rows = await fetchFeed(page, 24);
    const block = await pickBestEvent(page, rows, "block", { minStages: 3, requireLatency: true });
    const redact = await pickBestEvent(page, rows, "redact", { minStages: 3, requireLatency: true });
    const allow = await pickBestEvent(page, rows, "allow", {
      minStages: 3,
      requireRouting: true,
      requireLatency: true,
    });
    if (block && redact && allow) return { rows, block, redact, allow };
    await page.waitForTimeout(3000);
  }
  const rows = await fetchFeed(page, 168);
  return {
    rows,
    block: await pickBestEvent(page, rows, "block", { minStages: 3, requireLatency: true }),
    redact: await pickBestEvent(page, rows, "redact", { minStages: 3, requireLatency: true }),
    allow: await pickBestEvent(page, rows, "allow", {
      minStages: 3,
      requireRouting: true,
      requireLatency: true,
    }),
  };
}

async function openDetailedResults(page) {
  for (const tab of ["firewall-1-1", "firewall-1-7", "firewall-1-2"]) {
    await page.goto(`${BASE}/?tab=${tab}`, { waitUntil: "domcontentloaded", timeout: 120000 });
    // Module-1.1 is telemetry-heavy — allow ≥60s for a slow-but-correct render.
    await page.waitForTimeout(8000);
    const openBtn = page.getByRole("button", { name: /Open detailed results/i }).first();
    if (!(await openBtn.count())) continue;
    await openBtn.scrollIntoViewIfNeeded().catch(() => {});
    await openBtn.click({ timeout: 15000 });
    const ok = await page.locator("text=/Detailed Records/i").first()
      .waitFor({ state: "visible", timeout: 60000 }).then(() => true).catch(() => false);
    if (!ok) continue;
    const rowCount = await waitForRows(page, 1, 60000);
    if (rowCount > 0) return true;
  }
  return false;
}

async function waitForRows(page, min = 1, timeoutMs = 45000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const n = await page.locator("tbody tr").count();
    if (n >= min) return n;
    await page.waitForTimeout(400);
  }
  return page.locator("tbody tr").count();
}

async function openEvent(page, ev) {
  const meta = ev?.metadata || {};
  const reqId = meta.request_id || ev?.request_id || String(ev.id);
  const search = page.getByPlaceholder(/Search all fields/i);
  if (await search.count()) {
    await search.fill("");
    await page.waitForTimeout(400);
    for (const term of [reqId, String(ev.id)]) {
      await search.fill("");
      await search.fill(String(term));
      await page.waitForTimeout(1200);
      const rows = await page.locator("tbody tr").count();
      if (rows === 0) continue;
      await page.locator("tbody tr").first().click();
      const ok = await page.getByRole("heading", { name: /Scan Detail Report/i }).first()
        .waitFor({ state: "visible", timeout: 45000 }).then(() => true).catch(() => false);
      if (ok) return true;
      await page.getByRole("button", { name: /Back to Activity Preview/i }).first().click().catch(() => {});
      await page.waitForTimeout(500);
    }
  }
  return false;
}

async function waitForPipelineTrace(page) {
  await page.locator("[data-testid='log-detail-pipeline-stages']").first()
    .waitFor({ state: "visible", timeout: 30000 });
  const hasTrace = await page.locator("[data-testid='pipeline-trace-view']").first()
    .waitFor({ state: "visible", timeout: 45000 }).then(() => true).catch(() => false);
  if (hasTrace) return true;
  const bodyText = await page.locator("body").innerText();
  if (/Pipeline Stages \(\d+\)/.test(bodyText) && !/No per-stage pipeline trace/.test(bodyText)) {
    await page.locator("[data-testid='log-detail-pipeline-stages'] button").first().click().catch(() => {});
    await page.waitForTimeout(500);
    return page.locator("[data-testid='pipeline-trace-view']").first()
      .waitFor({ state: "visible", timeout: 15000 }).then(() => true).catch(() => false);
  }
  return false;
}

async function expandInputOutput(page) {
  const heading = page.getByRole("button", { name: /Input \/ Output/i });
  if (await heading.count()) {
    const expanded = await heading.getAttribute("aria-expanded");
    if (expanded === "false") await heading.click();
    await page.waitForTimeout(300);
  }
}

async function readUiDurations(page) {
  const durationLine = (await page.locator("text=/Duration:/").first().textContent().catch(() => "")) || "";
  const headerMs = parseDurationMs(durationLine);
  const metricCard = page.locator("text=Total Duration").locator("..").locator("..");
  const metricText = (await metricCard.textContent().catch(() => "")) || "";
  const metricMs = parseDurationMs(metricText);
  return { durationLine: durationLine.trim(), headerMs, metricMs };
}

async function verifyEventDetail(page, kind, picked) {
  const detail = await fetchEventDetail(page, picked.ev.id);
  const ev = detail || picked.ev;
  const trace = extractTrace(ev);
  const apiTotalMs = resolveApiTotalMs(trace);
  const stages = stageCount(trace);

  const opened = await openEvent(page, ev);
  if (!opened) return { kind, pass: false, reason: "could not open Scan Detail" };

  const traceReady = await waitForPipelineTrace(page);
  if (!traceReady) return { kind, pass: false, reason: "pipeline trace not visible" };

  await page.locator("[data-testid='pipeline-stage-timeline']").first()
    .waitFor({ state: "visible", timeout: 20000 });
  const stageButtons = await page.locator("[data-testid='pipeline-stage-timeline'] button").count();

  await expandInputOutput(page);
  const bodyText = await page.locator("body").innerText();
  const ui = await readUiDurations(page);

  const latencyOk = apiTotalMs != null && apiTotalMs > 0
    ? withinTolerance(apiTotalMs, ui.headerMs) && withinTolerance(apiTotalMs, ui.metricMs)
    : false;

  let specificOk = false;
  let specificReason = "";

  if (kind === "block") {
    specificOk = /withheld|not delivered|blocked/i.test(bodyText)
      && (/Input \(prompt\)|Input \(before redaction\)/i.test(bodyText));
    specificReason = "withheld banner + input panel";
  } else if (kind === "redact") {
    const ioBeforeAfter = /before redaction/i.test(bodyText) && /forwarded to model/i.test(bodyText);
    let stageBeforeAfter = false;
    let stageRedactReason = false;
    const redactStage = page.locator("[data-testid='pipeline-stage-timeline'] button").filter({
      hasText: /Policy|Input Scan|Output Guard|redact/i,
    }).first();
    if (await redactStage.count()) {
      await redactStage.click();
      await page.waitForTimeout(400);
      const stageText = await page.locator("body").innerText();
      stageBeforeAfter = /Before|Scanned input/i.test(stageText)
        && /After policy redaction|Forwarded to model|After output guard/i.test(stageText);
      stageRedactReason = /enforcement:\s*REDACT|redact(ed|ion)|PII detected|Policy engine redacted/i.test(stageText);
    }
    const redactStageCount = await page.locator("[data-testid='pipeline-stage-timeline'] button").filter({
      hasText: /REDACT|Redact/i,
    }).count();
    specificOk = /Output \(response\)/i.test(bodyText)
      && redactStageCount >= 1
      && (ioBeforeAfter || stageBeforeAfter || stageRedactReason);
    specificReason = ioBeforeAfter
      ? "trace-root before/after + output"
      : stageBeforeAfter
        ? "stage before/after + output"
        : "redact stage reason + output";
  } else if (kind === "allow") {
    const routingCount = await page.locator("[data-testid='routing-decision-card']").count();
    specificOk = routingCount > 0 && /Routing decision/i.test(bodyText);
    specificReason = "routing decision card";
  }

  await page.screenshot({
    path: path.join(SHOT_DIR, `${kind}-scan-detail.png`),
    fullPage: false,
  });

  return {
    kind,
    id: ev.id,
    requestId: ev?.metadata?.request_id || ev?.request_id,
    pass: stages >= 2 && stageButtons >= 2 && latencyOk && specificOk,
    stages,
    stageButtons,
    apiTotalMs,
    headerMs: ui.headerMs,
    metricMs: ui.metricMs,
    latencyOk,
    specificOk,
    specificReason,
    hasRouting: hasRouting(trace),
  };
}

async function backToList(page) {
  const back = page.getByRole("button", { name: /Back to Activity Preview/i }).first();
  if (await back.count()) await back.click();
  await page.waitForTimeout(800);
  const search = page.getByPlaceholder(/Search all fields/i);
  if (await search.count()) {
    await search.fill("");
    await page.waitForTimeout(600);
  }
}

async function main() {
  const report = {
    ok: false,
    checkpoint: "pipeline-p25",
    blocked: null,
    redact: null,
    allow: null,
    triggered: null,
    consoleErrors: [],
  };

  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const { browser, page } = await launchBrowser();
  page.on("pageerror", (e) => report.consoleErrors.push(`pageerror: ${String(e.message || e).slice(0, 160)}`));
  page.on("console", (m) => {
    if (m.type() === "error" && !TRANSIENT_NET.test(m.text()) && !/400|401|429/.test(m.text())) {
      report.consoleErrors.push(`console.error: ${m.text().slice(0, 160)}`);
    }
  });

  try {
    await login(page);

    let blockedPick = await pickBestEvent(page, await fetchFeed(page, 168), "block", {
      minStages: 3,
      requireLatency: true,
    });
    let redactPick = await pickBestEvent(page, await fetchFeed(page, 168), "redact", {
      minStages: 3,
      requireLatency: true,
    });
    let allowPick = await pickBestEvent(page, await fetchFeed(page, 168), "allow", {
      minStages: 3,
      requireRouting: true,
      requireLatency: true,
    });

    if (!blockedPick || !redactPick || !allowPick) {
      const missing = [];
      if (!blockedPick) missing.push("block");
      if (!redactPick) missing.push("redact");
      if (!allowPick) missing.push("allow");
      report.triggered = [];
      if (missing.includes("block")) {
        report.triggered.push({ kind: "block", http: await runAttackScenario(page, /Prompt Injection/) });
      }
      if (missing.includes("redact")) {
        report.triggered.push({ kind: "redact", http: await runAttackScenario(page, /Sensitive Data Leakage/) });
      }
      if (missing.includes("allow")) {
        report.triggered.push({ kind: "allow", http: await runAttackScenario(page, /Clean Prompt \(Safe\)/) });
      }
      const polled = await pollForEvents(page, 25);
      blockedPick = blockedPick || polled.block;
      redactPick = redactPick || polled.redact;
      allowPick = allowPick || polled.allow;
    }

    if (!blockedPick || !redactPick || !allowPick) {
      throw new Error(
        `Missing sample events: block=${!!blockedPick} redact=${!!redactPick} allow=${!!allowPick}`,
      );
    }

    const openedList = await openDetailedResults(page);
    if (!openedList) throw new Error("Could not open Detailed Records");
    await waitForRows(page, 1);

    report.blocked = await verifyEventDetail(page, "block", blockedPick);
    await backToList(page);

    report.redact = await verifyEventDetail(page, "redact", redactPick);
    await backToList(page);

    report.allow = await verifyEventDetail(page, "allow", allowPick);

    report.noConsoleErrors = report.consoleErrors.length === 0;
    report.pipelineP25Pass = report.noConsoleErrors
      && report.blocked?.pass
      && report.redact?.pass
      && report.allow?.pass;
    report.ok = report.pipelineP25Pass;

    console.log(JSON.stringify({
      pipelineP25Pass: report.pipelineP25Pass,
      blocked: report.blocked,
      redact: report.redact,
      allow: report.allow,
      triggered: report.triggered,
      consoleErrors: report.consoleErrors.slice(0, 4),
    }, null, 2));
  } catch (err) {
    report.error = String(err?.message || err);
    console.error("PIPELINE-P25 FAIL:", report.error);
  } finally {
    await browser.close();
  }

  fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
  process.exit(report.ok ? 0 : 1);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
