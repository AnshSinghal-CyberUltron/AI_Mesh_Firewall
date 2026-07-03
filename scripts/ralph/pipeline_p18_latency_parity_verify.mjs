/**
 * PIPELINE-0018 browser gate: backend pipeline_trace.total_latency_ms == UI Duration.
 * Run: BASE_URL=http://127.0.0.1:8180 node scripts/ralph/pipeline_p18_latency_parity_verify.mjs
 *
 * Acceptable rounding: |api_ms - ui_ms| <= 0.1ms (matches formatPipelineDurationMs).
 */
import fs from "node:fs";
import { login, launchBrowser, BASE } from "./mcp_page_typesim.mjs";

const OUT = process.env.E2E_REPORT || "mcp-parallel/findings/pipeline-p18-latency-parity/evidence.json";
const TOLERANCE_MS = 0.1;

function extractTrace(ev) {
  const meta = ev?.metadata || {};
  const extra = meta.extra || {};
  const zs = meta.zeroshield || extra.zeroshield || {};
  return meta.pipeline_trace || extra.pipeline_trace || zs.pipeline_trace || {};
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

async function authHeaders(page) {
  const token = await page.evaluate(() => localStorage.getItem("auth_access"));
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function findEventWithLatency(page) {
  const res = await page.request.get(
    `${BASE}/api/security/threat-feed/?hours=24&limit=500`,
    { headers: await authHeaders(page) },
  );
  if (!res.ok()) return null;
  const body = await res.json();
  const rows = Array.isArray(body) ? body : (body?.results || []);
  let best = null;
  let bestTotal = 0;
  for (const ev of rows) {
    const trace = extractTrace(ev);
    const total = Number(trace.total_latency_ms || 0);
    const stages = Array.isArray(trace.stages) ? trace.stages : [];
    const maxStage = stages.reduce((m, s) => Math.max(m, Number(s?.latency_ms || 0)), 0);
    const score = total || maxStage;
    if (score > bestTotal && total > 0) {
      bestTotal = total;
      best = { id: ev.id, requestId: ev?.metadata?.request_id, apiTotalMs: total, trace };
    }
  }
  return best;
}

async function fetchDetail(page, id) {
  const res = await page.request.get(
    `${BASE}/api/security/threat-feed/${id}/`,
    { headers: await authHeaders(page) },
  );
  if (!res.ok()) return null;
  return res.json();
}

async function openEventBySearch(page, needles) {
  const terms = (Array.isArray(needles) ? needles : [needles]).filter(Boolean);
  if (!terms.length) return false;
  const search = page.getByPlaceholder(/Search all fields/i);
  if (!(await search.count())) return false;
  for (const needle of terms) {
    await search.fill(String(needle));
    await page.waitForTimeout(600);
    if ((await page.locator("tbody tr").count()) === 0) continue;
    await page.locator("tbody tr").first().click();
    const opened = await page.locator("text=/Scan Detail Report/i").first()
      .waitFor({ state: "visible", timeout: 15000 }).then(() => true).catch(() => false);
    if (opened) return true;
    await page.getByRole("button", { name: /Back to Activity Preview/i }).first().click().catch(() => {});
    await page.waitForTimeout(300);
  }
  return false;
}

async function readUiDurations(page) {
  const durationLine = (await page.locator("text=/Duration:/").first().textContent().catch(() => "")) || "";
  const headerMs = parseDurationMs(durationLine);
  const metricCard = page.locator("text=Total Duration").locator("..").locator("..");
  const metricText = (await metricCard.textContent().catch(() => "")) || "";
  const metricMs = parseDurationMs(metricText);
  return { durationLine: durationLine.trim(), headerMs, metricMs, metricText: metricText.trim() };
}

async function main() {
  const report = {
    ok: false,
    base: BASE,
    toleranceMs: TOLERANCE_MS,
    steps: [],
    parity: null,
    error: null,
  };
  const { browser, page } = await launchBrowser();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (msg) => {
    if (msg.type() === "error" && !/400|401|429/.test(msg.text())) errors.push(msg.text());
  });

  try {
    await login(page);
    report.steps.push("login");

    let verified = false;
    for (const tab of ["firewall-1-1", "firewall-1-7"]) {
      await page.goto(`${BASE}/?tab=${tab}`, { waitUntil: "networkidle", timeout: 120000 });
      report.steps.push(`tab-${tab}`);

      const openBtn = page.getByRole("button", { name: /Open detailed results/i });
      if (!(await openBtn.count())) continue;
      await openBtn.first().click();
      await page.locator("text=/Detailed Records/i").first().waitFor({ state: "visible", timeout: 60000 });
      report.steps.push(`detailed-records-${tab}`);

      const candidate = await findEventWithLatency(page);
      if (!candidate) continue;

      const detail = await fetchDetail(page, candidate.id);
      const detailTrace = detail ? extractTrace(detail) : {};
      const apiTotalMs = Number(detailTrace.total_latency_ms ?? candidate.apiTotalMs);
      if (!(apiTotalMs > 0)) continue;

      report.candidate = { id: candidate.id, requestId: candidate.requestId, apiTotalMs };
      report.steps.push(`api-event-${candidate.id}`);

      const opened = await openEventBySearch(page, [candidate.id, candidate.requestId]);
      if (!opened) continue;

      await page.locator("text=/Scan Detail Report/i").first().waitFor({ state: "visible", timeout: 15000 });
      await page.waitForTimeout(800);

      const ui = await readUiDurations(page);
      report.ui = ui;

      const headerOk = withinTolerance(apiTotalMs, ui.headerMs);
      const metricOk = withinTolerance(apiTotalMs, ui.metricMs);
      const headerMetricOk = ui.headerMs != null && ui.metricMs != null
        ? withinTolerance(ui.headerMs, ui.metricMs)
        : false;

      report.parity = {
        apiTotalMs,
        headerMs: ui.headerMs,
        metricMs: ui.metricMs,
        headerMatchesApi: headerOk,
        metricMatchesApi: metricOk,
        headerMatchesMetric: headerMetricOk,
        deltaHeader: ui.headerMs != null ? Math.abs(apiTotalMs - ui.headerMs) : null,
        deltaMetric: ui.metricMs != null ? Math.abs(apiTotalMs - ui.metricMs) : null,
      };

      fs.mkdirSync("mcp-parallel/findings/pipeline-p18-latency-parity", { recursive: true });
      await page.screenshot({
        path: "mcp-parallel/findings/pipeline-p18-latency-parity/scan-detail-duration.png",
        fullPage: false,
      });

      if (errors.length) throw new Error(`Console errors: ${errors.join("; ")}`);
      if (!headerOk) {
        throw new Error(
          `Duration header mismatch: api=${apiTotalMs} ui=${ui.headerMs} delta=${report.parity.deltaHeader}`,
        );
      }
      if (!metricOk) {
        throw new Error(
          `Total Duration metric mismatch: api=${apiTotalMs} ui=${ui.metricMs} delta=${report.parity.deltaMetric}`,
        );
      }
      if (!headerMetricOk) {
        throw new Error(`Header vs metric mismatch: header=${ui.headerMs} metric=${ui.metricMs}`);
      }

      verified = true;
      report.ok = true;
      break;
    }

    if (!verified) throw new Error("No event with pipeline latency found for parity check");
  } catch (err) {
    report.error = String(err?.stack || err);
  } finally {
    fs.mkdirSync("mcp-parallel/findings/pipeline-p18-latency-parity", { recursive: true });
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    await browser.close();
  }

  console.log(JSON.stringify(report, null, 2));
  if (!report.ok) process.exitCode = 1;
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
