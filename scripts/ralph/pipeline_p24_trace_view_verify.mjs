/**
 * PIPELINE-0024 browser gate: LogDetail pipeline-trace view — aligned, responsive,
 * both themes, detector-clean. Sweeps Scan Detail @ 1440/1024/768/375 × light/dark.
 *
 * Run: NODE_PATH="$PWD/tests/e2e/node_modules" BASE_URL=http://127.0.0.1:8180 \
 *   node scripts/ralph/pipeline_p24_trace_view_verify.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { login, launchBrowser, BASE } from "./mcp_page_typesim.mjs";

const OUT = process.env.E2E_REPORT || "mcp-parallel/findings/pipeline-p24-trace-view/evidence.json";
const SHOT_DIR = process.env.SHOT_DIR || "mcp-parallel/findings/pipeline-p24-trace-view";
const VIEWPORTS = [[1440, 1000], [1024, 768], [768, 1024], [375, 812]];
const TRANSIENT_NET = /ERR_NETWORK_CHANGED|ERR_INTERNET_DISCONNECTED|ERR_IO_SUSPENDED|net::ERR_ABORTED/i;

async function authHeaders(page) {
  const token = await page.evaluate(() => localStorage.getItem("auth_access"));
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function fetchFeed(page, hours = 24) {
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

function extractTrace(ev) {
  const meta = ev?.metadata || {};
  const extra = meta.extra || {};
  return meta.pipeline_trace || extra.pipeline_trace || ev?.pipeline_trace || {};
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

function pickTraceEvent(rows) {
  for (const ev of rows) {
    const trace = extractTrace(ev);
    const stages = Array.isArray(trace.stages) ? trace.stages : [];
    if (stages.length >= 3) {
      const meta = ev?.metadata || {};
      const extra = meta.extra || {};
      return {
        id: ev.id,
        requestId: meta.request_id || extra.request_id || ev.request_id,
        stageCount: stages.length,
      };
    }
  }
  return null;
}

async function pickTraceEventWithDetail(page, rows) {
  let picked = pickTraceEvent(rows);
  if (picked) return picked;
  for (const ev of rows.slice(0, 40)) {
    const detail = await fetchEventDetail(page, ev.id);
    if (!detail) continue;
    const trace = extractTrace(detail);
    const stages = Array.isArray(trace.stages) ? trace.stages : [];
    if (stages.length >= 3) {
      const meta = detail.metadata || {};
      const extra = meta.extra || {};
      return {
        id: detail.id || ev.id,
        requestId: meta.request_id || extra.request_id || detail.request_id,
        stageCount: stages.length,
      };
    }
  }
  return null;
}

async function openDetailedResults(page) {
  for (const tab of ["firewall-1-1", "firewall-1-7", "firewall-1-2"]) {
    await page.goto(`${BASE}/?tab=${tab}`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.waitForTimeout(3000);
    const openBtn = page.getByRole("button", { name: /Open detailed results/i }).first();
    if (!(await openBtn.count())) continue;
    await openBtn.scrollIntoViewIfNeeded().catch(() => {});
    await openBtn.click({ timeout: 10000 });
    const ok = await page.locator("text=/Detailed Records/i").first()
      .waitFor({ state: "visible", timeout: 30000 }).then(() => true).catch(() => false);
    if (!ok) continue;
    const rowCount = await waitForRows(page, 1, 45000);
    if (rowCount > 0) return true;
  }
  return false;
}

async function openEventByRequestId(page, event) {
  const search = page.getByPlaceholder(/Search all fields/i);
  if (!(await search.count())) return false;
  for (const term of [event?.requestId, event?.id].filter(Boolean)) {
    await search.fill("");
    await search.fill(String(term));
    await page.waitForTimeout(900);
    if ((await page.locator("tbody tr").count()) === 0) continue;
    await page.locator("tbody tr").first().click();
    const ok = await page.getByRole("heading", { name: /Scan Detail Report/i }).first()
      .waitFor({ state: "visible", timeout: 30000 }).then(() => true).catch(() => false);
    if (ok) return true;
    await page.getByRole("button", { name: /Back to Activity Preview/i }).first().click().catch(() => {});
  }
  return false;
}

async function openScanDetail(page, event) {
  if (event && await openEventByRequestId(page, event)) return true;
  if ((await page.locator("tbody tr").count()) > 0) {
    await page.locator("tbody tr").first().click();
    return page.getByRole("heading", { name: /Scan Detail Report/i }).first()
      .waitFor({ state: "visible", timeout: 30000 }).then(() => true).catch(() => false);
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

async function measure(page) {
  return page.evaluate(() => {
    const vw = window.innerWidth;
    const pageOverflow = document.documentElement.scrollWidth > vw + 2;
    let bleed = 0;
    for (const el of document.querySelectorAll("main *")) {
      const cls = (el.className || "").toString();
      if (/glow|hero-glow|blur-/i.test(cls)) continue;
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.right > vw + 4) {
        const oxOk = getComputedStyle(el).overflowX;
        const parentScrolls = el.closest("[class*='overflow-x-auto'],[class*='overflow-auto'],[class*='overflow-x-scroll']");
        if (!parentScrolls && oxOk !== "auto" && oxOk !== "scroll") bleed++;
      }
    }
    return { pageOverflow, bleed, vw };
  });
}

async function minTouch(page) {
  return page.evaluate(() => {
    let min = 9999;
    for (const b of document.querySelectorAll("[data-testid='pipeline-trace-view'] button, [data-testid='log-detail-pipeline-stages'] button")) {
      const r = b.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) min = Math.min(min, Math.min(r.width, r.height));
    }
    return min === 9999 ? null : Math.round(min);
  });
}

async function main() {
  const report = {
    ok: false,
    checkpoint: "pipeline-p24",
    sweep: [],
    consoleErrors: [],
    pickedEvent: null,
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
    let rows = await fetchFeed(page, 24);
    report.pickedEvent = await pickTraceEventWithDetail(page, rows);
    if (!report.pickedEvent) {
      rows = await fetchFeed(page, 168);
      report.pickedEvent = await pickTraceEventWithDetail(page, rows);
    }
    if (!report.pickedEvent) throw new Error("No event with pipeline trace (3+ stages) in feed");

    const openedList = await openDetailedResults(page);
    if (!openedList) throw new Error("Could not open Detailed Records");
    await waitForRows(page, 1);

    const openedDetail = await openScanDetail(page, report.pickedEvent);
    if (!openedDetail) throw new Error("Could not open Scan Detail Report");

    const traceReady = await waitForPipelineTrace(page);
    if (!traceReady) throw new Error("Pipeline trace view not visible for picked event");

    await page.locator("[data-testid='pipeline-stage-timeline']").first()
      .waitFor({ state: "visible", timeout: 20000 });

    for (const theme of ["light", "dark"]) {
      await page.evaluate((t) => {
        document.documentElement.classList.toggle("dark", t === "dark");
      }, theme);

      for (const [w, h] of VIEWPORTS) {
        await page.setViewportSize({ width: w, height: h });
        await page.waitForTimeout(400);
        const layout = await measure(page);
        const touch = w <= 768 ? await minTouch(page) : null;
        const stageButtons = await page.locator("[data-testid='pipeline-stage-timeline'] button").count();
        const hasRouting = await page.locator("[data-testid='routing-decision-card']").count();
        const bodyHasStages = /Pipeline Stages/i.test(await page.locator("body").innerText());

        await page.screenshot({
          path: path.join(SHOT_DIR, `${theme}-${w}x${h}-trace-view.png`),
          fullPage: false,
        });

        report.sweep.push({
          theme,
          w,
          h,
          pageOverflow: layout.pageOverflow,
          bleed: layout.bleed,
          minTouch: touch,
          stageButtons,
          hasRouting: hasRouting > 0,
          bodyHasStages,
        });
      }
    }

    report.noConsoleErrors = report.consoleErrors.length === 0;
    report.noOverflow = report.sweep.every((s) => !s.pageOverflow);
    report.noBleed = report.sweep.every((s) => s.bleed === 0);
    report.touchOk = report.sweep.filter((s) => s.minTouch != null).every((s) => s.minTouch >= 24);
    report.traceVisible = report.sweep.every((s) => s.stageButtons >= 3 && s.bodyHasStages);
    report.pipelineP24Pass = report.noConsoleErrors && report.noOverflow && report.noBleed && report.touchOk && report.traceVisible;
    report.ok = report.pipelineP24Pass;

    console.log(JSON.stringify({
      pipelineP24Pass: report.pipelineP24Pass,
      noOverflow: report.noOverflow,
      noBleed: report.noBleed,
      noConsoleErrors: report.noConsoleErrors,
      touchOk: report.touchOk,
      traceVisible: report.traceVisible,
      pickedEvent: report.pickedEvent,
      sweep: report.sweep,
      consoleErrors: report.consoleErrors.slice(0, 4),
    }, null, 2));
  } catch (err) {
    report.error = String(err?.message || err);
    console.error("PIPELINE-P24 FAIL:", report.error);
  } finally {
    await browser.close();
  }

  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
  process.exit(report.ok ? 0 : 1);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
