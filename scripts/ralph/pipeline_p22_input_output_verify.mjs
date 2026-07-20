/**
 * PIPELINE-0022 browser gate: LogDetail Input/Output panels show trace-root I/O.
 * Run: NODE_PATH="$PWD/tests/e2e/node_modules" BASE_URL=http://127.0.0.1:8180 \
 *   node scripts/ralph/pipeline_p22_input_output_verify.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { login, launchBrowser, BASE } from "./mcp_page_typesim.mjs";

const OUT = process.env.E2E_REPORT || "mcp-parallel/findings/pipeline-p22-input-output/evidence.json";

function extractTrace(ev) {
  const meta = ev?.metadata || {};
  const extra = meta.extra || {};
  return meta.pipeline_trace || extra.pipeline_trace || {};
}

async function authHeaders(page) {
  const token = await page.evaluate(() => localStorage.getItem("auth_access"));
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function fetchFeed(page) {
  const res = await page.request.get(
    `${BASE}/api/security/threat-feed/?hours=168&limit=500`,
    { headers: await authHeaders(page) },
  );
  if (!res.ok()) return [];
  const body = await res.json();
  return Array.isArray(body) ? body : (body?.results || []);
}

function pickByAction(rows, action) {
  const want = String(action || "").toLowerCase();
  for (const ev of rows) {
    const act = String(ev?.action || ev?.metadata?.action || "").toLowerCase();
    if (act === want || act.includes(want)) {
      const meta = ev?.metadata || {};
      const hasContent = meta.prompt_snippet || meta.prompt_submitted || meta.input_text
        || extractTrace(ev)?.input_text || extractTrace(ev)?.prompt_preview;
      if (hasContent) return ev;
    }
  }
  return null;
}

async function waitForRows(page, min = 1, timeoutMs = 30000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const n = await page.locator("tbody tr").count();
    if (n >= min) return n;
    await page.waitForTimeout(400);
  }
  return await page.locator("tbody tr").count();
}

async function openDetailedResults(page) {
  await page.goto(`${BASE}/?tab=firewall-1-1`, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.waitForTimeout(2000);
  const openBtn = page.getByRole("button", { name: /Open detailed results/i }).first();
  if (!(await openBtn.count())) return false;
  await openBtn.click();
  await page.locator("text=/Detailed Records/i").first().waitFor({ state: "visible", timeout: 30000 });
  await waitForRows(page, 1);
  return true;
}

async function openEventByRequestId(page, requestId) {
  const search = page.getByPlaceholder(/Search all fields/i);
  if (!(await search.count())) return false;
  await search.fill("");
  await search.fill(String(requestId));
  await page.waitForTimeout(800);
  if ((await page.locator("tbody tr").count()) === 0) return false;
  await page.locator("tbody tr").first().click();
  return page.getByRole("heading", { name: /Scan Detail Report/i }).first()
    .waitFor({ state: "visible", timeout: 30000 }).then(() => true).catch(() => false);
}

async function verifyAction(page, rows, actionFilter, actionName) {
  const ev = pickByAction(rows, actionName);
  if (!ev) return { pass: false, reason: `no ${actionName} event with content in feed` };

  const actionSelect = page.locator("select").first();
  if (await actionSelect.count()) {
    await actionSelect.selectOption(actionFilter).catch(() => {});
    await page.waitForTimeout(600);
  }
  await waitForRows(page, 1);

  let opened = false;
  if ((await page.locator("tbody tr").count()) > 0) {
    await page.locator("tbody tr").first().click();
    opened = await page.getByRole("heading", { name: /Scan Detail Report/i }).first()
      .waitFor({ state: "visible", timeout: 30000 }).then(() => true).catch(() => false);
  }

  if (!opened) {
    const reqId = ev?.metadata?.request_id || ev?.request_id || String(ev.id);
    opened = await openEventByRequestId(page, reqId);
  }

  if (!opened) {
    return {
      pass: false,
      reason: `could not open detail for ${actionName}`,
      id: ev.id,
      reqId: ev?.metadata?.request_id || ev?.request_id,
    };
  }

  await expandInputOutput(page);
  const bodyText = await page.locator("body").innerText();
  const trace = extractTrace(ev);

  if (actionName === "block") {
    return {
      id: ev.id,
      traceHasInputText: Boolean(trace?.input_text),
      uiHasInput: /Input \(prompt\)|Input \(before redaction\)/i.test(bodyText),
      uiHasWithheld: /withheld|not delivered|blocked/i.test(bodyText),
      pass: /Input \(prompt\)|Input \(before redaction\)/i.test(bodyText)
        && /withheld|not delivered|blocked/i.test(bodyText),
    };
  }

  return {
    id: ev.id,
    traceInputWasRedacted: trace?.input_was_redacted,
    uiHasBeforeAfter: /before redaction|forwarded to model|Input \(prompt\)/i.test(bodyText),
    uiHasOutput: /Output \(response\)/i.test(bodyText),
    pass: (/before redaction|forwarded to model|Input \(prompt\)/i.test(bodyText))
      && /Output \(response\)/i.test(bodyText),
  };
}

async function expandInputOutput(page) {
  const heading = page.getByRole("button", { name: /Input \/ Output/i });
  if (await heading.count()) {
    const expanded = await heading.getAttribute("aria-expanded");
    if (expanded === "false") await heading.click();
    await page.waitForTimeout(300);
  }
}

async function backToList(page) {
  const back = page.getByRole("button", { name: /Back to Activity Preview/i }).first();
  if (await back.count()) await back.click();
  await page.waitForTimeout(500);
}

async function main() {
  const evidence = {
    ok: false,
    blocked: null,
    redact: null,
    consoleErrors: [],
  };

  const { browser, page } = await launchBrowser();
  page.on("console", (msg) => {
    if (msg.type() === "error") evidence.consoleErrors.push(msg.text());
  });

  try {
    await login(page);
    const rows = await fetchFeed(page);
    evidence.feedCounts = {
      total: rows.length,
      block: rows.filter((e) => String(e?.action || "").toLowerCase().includes("block")).length,
      redact: rows.filter((e) => String(e?.action || "").toLowerCase().includes("redact")).length,
    };

    const openedList = await openDetailedResults(page);
    if (!openedList) {
      evidence.error = "Could not open Detailed Records view";
      throw new Error(evidence.error);
    }

    evidence.blocked = await verifyAction(page, rows, "block", "block");
    if (evidence.blocked?.pass) await backToList(page);

    evidence.redact = await verifyAction(page, rows, "redact", "redact");

    evidence.ok = Boolean(
      evidence.blocked?.pass
      && evidence.redact?.pass
      && evidence.consoleErrors.length === 0,
    );
  } catch (err) {
    evidence.error = String(err?.message || err);
  } finally {
    await browser.close();
  }

  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify(evidence, null, 2));
  process.exit(evidence.ok ? 0 : 1);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
