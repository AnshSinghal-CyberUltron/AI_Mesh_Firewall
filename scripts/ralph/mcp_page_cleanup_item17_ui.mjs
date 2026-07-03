/**
 * MCP-page cleanup items 17-18 — "Backend unreachable" banner reflects REAL state
 * and DEGRADES GRACEFULLY on slow responses instead of hard-flipping to Offline.
 *
 * Root cause (item 17): useBackendHealth flipped to "disconnected" on the FIRST
 * failure of any kind, so a single 5s health timeout while workers were saturated
 * (or one transient fresh-TCP blip — the dev proxy runs keep-alive off) painted the
 * whole app "Backend unreachable / Offline" even though /api/health/ was up (200 in
 * ~2ms, proven separately). Fix: distinguish slow (timeout/non-2xx → soft "degraded")
 * from a sustained connection failure (→ hard "disconnected" only after 2 misses).
 *
 * Proves LIVE, reading the sidebar "System Status" widget (line + label):
 *   A. backend up          → "All systems operational" / "Protected"  (Online)
 *   B. slow probe (>5s)    → "Backend slow to respond" / "Degraded"   (amber, NOT Offline)
 *   C. sustained outage    → soft "Degraded" first, then escalates to
 *                            "Backend unreachable" / "Offline" after the 2nd poll
 */
import fs from "node:fs";
import { launchBrowser, login, BASE } from "./mcp_page_typesim.mjs";

const OUT = process.env.SHOT_DIR || "mcp-parallel/findings/mcp-page/cleanup17";
const TAB = `${BASE}/?tab=firewall-1-4`;

async function readSysStatus(page) {
  return page.evaluate(() => {
    const h = [...document.querySelectorAll("h3")].find((e) => /System Status/i.test(e.textContent || ""));
    if (!h) return null;
    const box = h.parentElement;
    return {
      line: (box.querySelector("p")?.textContent || "").trim(),
      label: (box.querySelectorAll("span")[0]?.textContent || "").trim(),
    };
  });
}

// poll readSysStatus until predicate true (or timeout) — returns last reading
async function waitFor(page, pred, timeoutMs) {
  const t0 = Date.now();
  let last = null;
  while (Date.now() - t0 < timeoutMs) {
    last = await readSysStatus(page);
    if (last && pred(last)) return last;
    await page.waitForTimeout(500);
  }
  return last;
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const report = { checkpoint: "cleanup-17-18", tests: {}, ok: false };
  const { browser } = await launchBrowser();

  const runTest = async (name, setupRoute, navigateFirst = true) => {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 1200 } });
    const page = await ctx.newPage();
    try {
      await login(page);
      if (setupRoute) await setupRoute(page);
      await page.goto(TAB, { waitUntil: "domcontentloaded", timeout: 120000 });
      return { ctx, page };
    } catch (e) {
      await ctx.close();
      throw e;
    }
  };

  try {
    // ── A. backend UP → Online ────────────────────────────────────────────
    {
      const { ctx, page } = await runTest("A", null);
      const s = await waitFor(page, (x) => /Protected/i.test(x.label), 15000);
      await page.screenshot({ path: `${OUT}/A-connected.png` });
      report.tests.A = {
        reading: s,
        pass: !!s && /operational/i.test(s.line) && /Protected/i.test(s.label),
      };
      await ctx.close();
    }

    // ── B. SLOW probe (>5s) → Degraded (amber), NOT hard Offline ──────────
    {
      const { ctx, page } = await runTest("B", async (p) => {
        await p.route("**/api/health/**", async (route) => {
          await new Promise((r) => setTimeout(r, 7000)); // client's 5s AbortSignal fires first
          try { await route.fulfill({ status: 200, contentType: "application/json", body: '{"status":"ok"}' }); } catch { /* already aborted */ }
        });
      });
      const s = await waitFor(page, (x) => /Degraded/i.test(x.label), 14000);
      await page.screenshot({ path: `${OUT}/B-degraded-on-slow.png` });
      report.tests.B = {
        reading: s,
        pass: !!s && /slow to respond/i.test(s.line) && /Degraded/i.test(s.label) && !/unreachable/i.test(s.line),
      };
      await ctx.close();
    }

    // ── C. SUSTAINED outage → Degraded first, then Offline after 2nd poll ─
    {
      const { ctx, page } = await runTest("C", async (p) => {
        await p.route("**/api/health/**", (route) => route.abort());
      });
      // first miss must be SOFT (degraded), never an immediate hard "unreachable"
      const first = await waitFor(page, (x) => /Degraded|Offline/i.test(x.label), 10000);
      const softFirst = !!first && /Degraded/i.test(first.label);
      await page.screenshot({ path: `${OUT}/C1-degraded-first.png` });
      // then it escalates to hard Offline once the 2nd poll (30s interval) also fails
      const escalated = await waitFor(page, (x) => /Offline/i.test(x.label), 42000);
      await page.screenshot({ path: `${OUT}/C2-offline-after-escalation.png` });
      report.tests.C = {
        first,
        escalated,
        softFirst,
        pass: softFirst && !!escalated && /unreachable/i.test(escalated.line) && /Offline/i.test(escalated.label),
      };
      await ctx.close();
    }

    report.item1718Pass = report.tests.A.pass && report.tests.B.pass && report.tests.C.pass;
    report.ok = true;
    console.log(JSON.stringify(report, null, 2));
    console.log(
      report.item1718Pass
        ? `CLEANUP-17-18: PASS — up→"${report.tests.A.reading?.label}"; slow→"${report.tests.B.reading?.label}" (soft, not Offline); outage→degraded-then-"${report.tests.C.escalated?.label}"`
        : "CLEANUP-17-18: FAIL",
    );
  } catch (e) {
    report.error = e.message;
    console.error("CLEANUP-17-18 FAIL:", e.message);
  } finally {
    fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.item1718Pass ? 0 : 1);
  }
}
main();
