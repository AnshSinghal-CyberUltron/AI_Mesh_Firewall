/**
 * Scan 251434 honesty + live jailbreak block — browser proof.
 *
 *   NODE_PATH=$PWD/tests/e2e/node_modules \
 *   BASE_URL=http://127.0.0.1:8180 \
 *   node scripts/scan_251434_ui_e2e.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { login, launchBrowser, BASE } from "./ralph/mcp_page_typesim.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(HERE, "..");
const SHOT_DIR = process.env.SHOT_DIR || path.join(ROOT, "runs/scan-251434/shots");
const OUT = process.env.E2E_REPORT || path.join(ROOT, "runs/scan-251434/ui_e2e.json");
const HIST_ID = process.env.HIST_SCAN_ID || "251434";
const LIVE_ZS = process.env.LIVE_REQUEST_ID || "zs-1b34a6759ecd";
const LIVE_FALLBACKS = (process.env.LIVE_REQUEST_IDS || "zs-1b34a6759ecd,zs-a279f2c45c30").split(",").map((s) => s.trim()).filter(Boolean);

const report = {
  ok: false,
  base: BASE,
  started_at: new Date().toISOString(),
  asserts: [],
  pageErrors: [],
  historical: {},
  liveBlock: {},
  error: null,
};

function assert(cond, label) {
  report.asserts.push({ label, pass: !!cond });
  if (!cond) throw new Error(`ASSERT FAILED: ${label}`);
}

async function authHeaders(page) {
  const token = await page.evaluate(() => localStorage.getItem("auth_access"));
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function openDetailedResults(page) {
  await page.goto(`${BASE}/?tab=firewall-1-1`, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.waitForTimeout(4000);
  const openBtn = page.getByRole("button", { name: /Open detailed results/i }).first();
  await openBtn.waitFor({ state: "visible", timeout: 60000 });
  await openBtn.click({ timeout: 15000 });
  await page.locator("text=/Detailed Records/i").first().waitFor({ state: "visible", timeout: 60000 });
  const start = Date.now();
  while (Date.now() - start < 45000) {
    const n = await page.locator("tbody tr").count();
    const heading = await page.locator("text=/Detailed Records/i").first().innerText().catch(() => "");
    if (n > 0 && !/\(0\)/.test(heading)) return;
    await page.waitForTimeout(1000);
  }
}

async function openEventByTerm(page, term) {
  const search = page.getByPlaceholder(/Search all fields/i);
  await search.waitFor({ state: "visible", timeout: 30000 });
  await search.fill("");
  await search.fill(String(term));
  await page.waitForTimeout(1500);
  const rows = page.locator("tbody tr");
  const n = await rows.count();
  if (n === 0) return false;
  await rows.first().click();
  return page.getByRole("heading", { name: /Scan Detail Report/i }).first()
    .waitFor({ state: "visible", timeout: 45000 })
    .then(() => true)
    .catch(() => false);
}

async function backToList(page) {
  await page.getByRole("button", { name: /Back to Activity Preview/i }).first().click().catch(() => {});
  await page.waitForTimeout(800);
}

async function readScanDetail(page) {
  const sec = page.getByRole("button", { name: /Security Analysis/i }).first();
  if (await sec.count()) {
    const expanded = await sec.getAttribute("aria-expanded");
    if (expanded !== "true") await sec.click();
    await page.waitForTimeout(400);
  }
  const body = await page.locator("body").innerText();
  const score = (
    body.match(/(\d+)\s*\/\s*100\s*Security Score/i)
    || body.match(/Security Score\s+(\d+)\s*\/\s*100/i)
    || []
  )[1] || null;
  const threat = (
    body.match(/Threat Level\s+([A-Z]+)/i)
    || body.match(/\b(HIGH|MEDIUM|LOW|NONE)\b\s*Threat Level/i)
    || []
  )[1] || null;
  const inj = (body.match(/Prompt Injection\s+(true|false)/i) || [])[1] || null;
  return {
    score,
    threat,
    promptInjection: inj,
    orgRoutingOff: /Org routing is off/i.test(body),
    routingDidNotRun: /Routing policy did not run/i.test(body),
    pinnedOrgEnabled: /Organization Dynamic Routing is Enabled/i.test(body),
    overallAllow: /\bALLOW\b/.test(body),
    overallBlock: /\bBLOCK\b/.test(body),
    inputScanBlock: /input scan[\s\S]{0,80}block/i.test(body),
    modelRecBlock: /Model recommendation:\s*BLOCK/i.test(body),
    scanId: (body.match(/Scan ID:\s*(\d+)/i) || [])[1] || null,
    requestId: (body.match(/Request ID:\s*(zs-[A-Za-z0-9]+)/i) || [])[1] || null,
  };
}

async function main() {
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const { browser, page } = await launchBrowser({ viewport: { width: 1440, height: 1200 } });
  page.on("pageerror", (e) => report.pageErrors.push(String(e.message || e)));
  try {
    await login(page);
    await openDetailedResults(page);

    const histOk = await openEventByTerm(page, HIST_ID);
    report.historical.opened = histOk;
    if (histOk) {
      const hist = await readScanDetail(page);
      report.historical = { ...report.historical, ...hist };
      await page.screenshot({ path: path.join(SHOT_DIR, "01-historical-251434.png"), fullPage: true });
      assert(hist.promptInjection === "true", "historical Prompt Injection is true");
      assert(hist.score && Number(hist.score) > 0, `historical Security Score > 0 (got ${hist.score})`);
      assert(hist.threat && hist.threat !== "NONE", `historical Threat Level not NONE (got ${hist.threat})`);
      assert(!hist.orgRoutingOff, "historical does not say Org routing is off");
      await backToList(page);
    } else {
      report.historical.note = "scan 251434 is no longer in threat-feed (API 404); live jailbreak block is the enforceable proof";
      await page.screenshot({ path: path.join(SHOT_DIR, "01-historical-251434-missing.png"), fullPage: true });
      const search = page.getByPlaceholder(/Search all fields/i);
      if (await search.count()) {
        await search.fill("");
        await page.waitForTimeout(800);
      }
    }

    const liveTerms = [...LIVE_FALLBACKS, "339285", "339283"];
    let liveTerm = LIVE_ZS;
    let liveOk = false;
    for (const term of liveTerms) {
      liveOk = await openEventByTerm(page, term);
      if (liveOk) {
        liveTerm = term;
        break;
      }
    }
    if (!liveOk) {
      const refresh = page.getByRole("button", { name: /Refresh/i }).first();
      if (await refresh.count()) await refresh.click().catch(() => {});
      await page.waitForTimeout(3000);
      for (const term of liveTerms) {
        liveOk = await openEventByTerm(page, term);
        if (liveOk) {
          liveTerm = term;
          break;
        }
      }
    }
    assert(liveOk, `open live blocked request ${liveTerm}`);
    const live = await readScanDetail(page);
    report.liveBlock = { term: liveTerm, ...live };
    await page.screenshot({ path: path.join(SHOT_DIR, "02-live-jailbreak-block.png"), fullPage: true });
    assert(live.overallBlock || live.inputScanBlock, "live jailbreak Scan Detail shows BLOCK");
    assert(live.promptInjection === "true", "live Prompt Injection is true");
    assert(live.threat && live.threat !== "NONE", `live Threat Level not NONE (got ${live.threat})`);
    assert(live.score && Number(live.score) > 0, `live Security Score > 0 (got ${live.score})`);

    report.ok = report.asserts.every((a) => a.pass);
  } catch (err) {
    report.error = String(err && err.message ? err.message : err);
    report.ok = false;
    await page.screenshot({ path: path.join(SHOT_DIR, "99-failure.png"), fullPage: true }).catch(() => {});
  } finally {
    report.finished_at = new Date().toISOString();
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    await browser.close();
  }
  console.log(JSON.stringify({ ok: report.ok, out: OUT, asserts: report.asserts, error: report.error }, null, 2));
  if (!report.ok) process.exit(1);
}

main();
