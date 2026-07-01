/**
 * M2.6 Incident Queue & Forensics — KPI load, filter, escalate, detail timeline, resolve sync gate.
 *
 * Run (host with playwright):
 *   NODE_PATH="$PWD/tests/e2e/node_modules" BASE_URL=http://127.0.0.1:8180 \
 *     node scripts/playwright_m26_incidents_sync.mjs
 *
 * Docker (no local node):
 *   docker run --rm --network ai_mesh_firewall_default -v "%CD%:/work" -w /work \
 *     mcr.microsoft.com/playwright:v1.60.0-jammy bash -lc \
 *     "cd tests/e2e && npm ci --omit=dev 2>/dev/null || npm install && \
 *      NODE_PATH=/work/tests/e2e/node_modules BASE_URL=http://frontend:5173 \
 *      M26_PROBE_STAMP=$M26_PROBE_STAMP node /work/scripts/playwright_m26_incidents_sync.mjs"
 */
import { chromium } from "playwright";
import { execSync } from "node:child_process";
import fs from "node:fs";

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const ORG_SLUG = process.env.ORG_SLUG || "zeroshield";
const OUT = process.env.E2E_REPORT || "runs/playwright_m26_incidents_sync.json";
const SHOT_DIR = process.env.SHOT_DIR || "runs/m26-incidents-sync";
const STAMP = process.env.M26_PROBE_STAMP || String(Date.now());
const PROBE_TITLE = process.env.M26_PROBE_TITLE || `M2.6 E2E probe ${STAMP}`;
const PRESEED_ID = process.env.M26_INCIDENT_ID ? Number(process.env.M26_INCIDENT_ID) : null;

const report = {
  base: BASE,
  ok: false,
  steps: [],
  asserts: [],
  pageErrors: [],
  notes: [],
  probeTitle: PROBE_TITLE,
  incidentId: null,
  error: null,
};
let CURRENT_PHASE = "init";

function assert(cond, label) {
  report.asserts.push({ label, pass: !!cond, phase: CURRENT_PHASE });
  if (!cond) throw new Error(`ASSERT FAILED [${CURRENT_PHASE}]: ${label}`);
  return cond;
}

async function shot(page, name) {
  try {
    fs.mkdirSync(SHOT_DIR, { recursive: true });
    await page.screenshot({ path: `${SHOT_DIR}/${name}.png`, fullPage: false });
  } catch {}
}

function seedOpenIncident() {
  if (PRESEED_ID) {
    report.incidentId = PRESEED_ID;
    report.notes.push(`using pre-seeded incident id=${PRESEED_ID} (host)`);
    return;
  }
  const py =
    `from auth.models import Organization; from policy.models import EnforcementEvent, SecurityIncident; ` +
    `from policy.constants import ACTION_BLOCK; title=${JSON.stringify(PROBE_TITLE)}; ` +
    `org=Organization.objects.filter(slug=${JSON.stringify(ORG_SLUG)}).first(); assert org; ` +
    `ev=EnforcementEvent.objects.create(organization=org, action=ACTION_BLOCK, metadata={` +
    `'source':'threat_intel','threat_type':'prompt_injection','detail':title,'model':'gpt-4o','key_prefix':'zs_m26'}); ` +
    `inc=SecurityIncident.objects.create(organization=org, enforcement_event=ev, title=title, severity='high', status='open'); ` +
    `print(inc.id)`;
  try {
    const cmd =
      `docker exec -w /app/control ai_mesh_firewall-control-1 python manage.py shell -c ` +
      JSON.stringify(py);
    const out = execSync(cmd, { encoding: "utf-8", timeout: 90000 });
    const idLine = (out || "")
      .split(/\r?\n/)
      .map((l) => l.trim())
      .reverse()
      .find((l) => /^\d+$/.test(l));
    if (!idLine) throw new Error(`no incident id in: ${out}`);
    report.incidentId = Number(idLine);
    report.notes.push(`seeded incident id=${report.incidentId}`);
  } catch (err) {
    report.notes.push(`seed skipped (${String(err?.message || err).slice(0, 140)})`);
  }
}

async function waitForControlPlane(page) {
  for (let i = 0; i < 30; i++) {
    const ok = await page.evaluate(async () => {
      try {
        const c = await fetch("/api/health/", { signal: AbortSignal.timeout(4000) });
        return c.ok;
      } catch {
        return false;
      }
    });
    if (ok) return true;
    await page.waitForTimeout(2000);
  }
  return false;
}

async function login(page) {
  CURRENT_PHASE = "login";
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded", timeout: 120000 });
  await waitForControlPlane(page);
  await page.locator("#email").fill(EMAIL);
  await page.locator("#password").fill(PASS);
  const [tokenResp] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST", {
      timeout: 45000,
    }),
    page.getByRole("button", { name: /^sign in$/i }).click(),
  ]);
  assert(tokenResp.ok(), `valid creds -> 2xx (got ${tokenResp.status()})`);
  await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 45000 });
  report.steps.push("login");
}

async function fetchIncidentSummary(page) {
  return page.evaluate(async () => {
    const tok = localStorage.getItem("auth_access");
    const r = await fetch(`/api/module2/incidents/?page=1&page_size=5&_=${Date.now()}`, {
      headers: { Authorization: `Bearer ${tok}` },
    });
    const body = await r.json().catch(() => ({}));
    return { status: r.status, summary: body.summary || {}, count: body.count ?? 0 };
  });
}

async function openIncidentsQueue(page) {
  CURRENT_PHASE = "m26-queue-load";
  const [listResp] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes("/api/module2/incidents/") && r.request().method() === "GET" && r.ok(),
      { timeout: 90000 },
    ),
    page.goto(`${BASE}/incidents`, { waitUntil: "domcontentloaded", timeout: 120000 }),
  ]);
  await page.getByRole("heading", { name: /Incident Queue/i }).waitFor({ state: "visible", timeout: 60000 });
  await page.getByText(/Active Queue/i).first().waitFor({ state: "visible", timeout: 30000 });

  const body = await listResp.json().catch(() => ({}));
  assert(body.summary && typeof body.summary.total === "number", "incidents API returns summary.total");
  assert(Array.isArray(body.results), "incidents API returns results array");
  report.steps.push("queue-load");
  await shot(page, "01-queue");
  return body.summary;
}

async function filterToProbe(page) {
  CURRENT_PHASE = "m26-search-filter";
  if (PRESEED_ID) {
    const data = await page.evaluate(async ({ probeTitle, incidentId }) => {
      const tok = localStorage.getItem("auth_access");
      const r = await fetch(`/api/module2/incidents/?search=${encodeURIComponent(probeTitle)}`, {
        headers: { Authorization: `Bearer ${tok}` },
      });
      const body = await r.json().catch(() => ({}));
      const row = (body.results || []).find((item) => item.id === incidentId) || (body.results || [])[0];
      return { status: r.status, row, results: body.results || [] };
    }, { probeTitle: PROBE_TITLE, incidentId: PRESEED_ID });
    assert(data.row, `pre-seeded incident visible in API search (id=${PRESEED_ID})`);
    report.incidentId = data.row.id;
    await page.getByPlaceholder(/Search title or notes/i).fill(PROBE_TITLE);
    await page.getByPlaceholder(/Search title or notes/i).press("Enter");
    await page.getByRole("link", { name: new RegExp(`#${data.row.id}`) }).waitFor({ state: "visible", timeout: 45000 });
    report.steps.push("search-filter");
    await shot(page, "02-search");
    return data.row;
  }
  const search = page.getByPlaceholder(/Search title or notes/i);
  await search.fill(PROBE_TITLE);
  const [searchResp] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes("/api/module2/incidents/") && r.url().includes("search=") && r.ok(),
      { timeout: 60000 },
    ),
    search.press("Enter"),
  ]);
  const data = await searchResp.json().catch(() => ({}));
  const row = (data.results || []).find((r) => r.title === PROBE_TITLE || String(r.title).includes(STAMP));
  assert(row, `search returns probe incident (${PROBE_TITLE})`);
  report.incidentId = row.id;
  await page.getByRole("link", { name: new RegExp(`#${row.id}`) }).waitFor({ state: "visible", timeout: 30000 });
  await page.getByText(/threat_intel|Threat Intel/i).first().waitFor({ state: "visible", timeout: 30000 });
  report.steps.push("search-filter");
  await shot(page, "02-search");
  return row;
}

async function investigateFromQueue(page, incidentId) {
  CURRENT_PHASE = "m26-investigate";
  const row = page.locator("tr").filter({ hasText: PROBE_TITLE }).first();
  const [invResp] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes(`/api/security/incidents/${incidentId}/investigate-incident/`) && r.ok(),
      { timeout: 60000 },
    ),
    row.getByRole("button", { name: /^Investigate$/i }).click(),
  ]);
  const invBody = await invResp.json().catch(() => ({}));
  assert(invBody.status === "investigating", `investigate API status investigating (got ${invBody.status})`);
  await page.getByText(new RegExp(`Incident #${incidentId} marked investigating`, "i")).waitFor({
    state: "visible",
    timeout: 30000,
  });
  report.steps.push("investigate-queue");
  await shot(page, "03-investigating");
}

async function escalateFromQueue(page, incidentId) {
  CURRENT_PHASE = "m26-escalate";
  const row = page.locator("tr").filter({ hasText: PROBE_TITLE }).first();
  const [escResp] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes(`/api/security/incidents/${incidentId}/escalate-incident/`) && r.ok(),
      { timeout: 60000 },
    ),
    row.getByRole("button", { name: /^Escalate$/i }).click(),
  ]);
  const escBody = await escResp.json().catch(() => ({}));
  assert(escBody.status === "escalated", `escalate API status escalated (got ${escBody.status})`);
  await page.getByText(new RegExp(`Incident #${incidentId} escalated`, "i")).waitFor({ state: "visible", timeout: 30000 });
  report.steps.push("escalate-queue");
  await shot(page, "04-escalated");
}

async function openDetailAndVerifyTimeline(page, incidentId) {
  CURRENT_PHASE = "m26-detail";
  const [detailResp] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes(`/api/module2/incidents/${incidentId}/`) && r.request().method() === "GET" && r.ok(),
      { timeout: 90000 },
    ),
    page.getByRole("link", { name: new RegExp(`#${incidentId}`) }).click(),
  ]);
  const detail = await detailResp.json().catch(() => ({}));
  assert(detail.incident?.id === incidentId, "detail page loads incident");
  assert(Array.isArray(detail.timeline) && detail.timeline.length > 0, "detail timeline non-empty");
  assert(detail.source === "threat_intel", `detail source threat_intel (got ${detail.source})`);

  await page.getByText(/Enforcement Timeline|Timeline/i).first().waitFor({ state: "visible", timeout: 30000 });
  await page.getByText(/gpt-4o/i).first().waitFor({ state: "visible", timeout: 30000 });
  report.steps.push("detail-timeline");
  await shot(page, "05-detail");
}

async function resolveFromDetail(page, incidentId) {
  CURRENT_PHASE = "m26-resolve";
  page.once("dialog", (dialog) => dialog.accept());
  const [resResp] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes(`/api/security/incidents/${incidentId}/resolve-incident/`) && r.ok(),
      { timeout: 60000 },
    ),
    page.getByRole("button", { name: /^Resolve$/i }).click(),
  ]);
  const resBody = await resResp.json().catch(() => ({}));
  assert(resBody.status === "resolved", `resolve API status resolved (got ${resBody.status})`);
  await page.getByText(/Status:\s*resolved/i).waitFor({ state: "visible", timeout: 45000 });
  report.steps.push("resolve-detail");
  await shot(page, "06-resolved");
}

async function verifyResolvedInQueue(page, incidentId) {
  CURRENT_PHASE = "m26-resolved-filter";
  await page.getByRole("link", { name: /Back to Queue/i }).click();
  await page.waitForURL(/\/incidents/, { timeout: 30000 });
  await page.getByRole("heading", { name: /Incident Queue/i }).waitFor({ state: "visible", timeout: 60000 });

  await page.locator("select").filter({ hasText: /All statuses/i }).first().selectOption("resolved");
  await page.getByPlaceholder(/Search title or notes/i).fill(PROBE_TITLE);
  await page.getByPlaceholder(/Search title or notes/i).press("Enter");

  await page.getByRole("link", { name: new RegExp(`#${incidentId}`) }).waitFor({ state: "visible", timeout: 45000 });
  await page.getByText(/resolved/i).first().waitFor({ state: "visible", timeout: 30000 });
  report.steps.push("resolved-filter");
  await shot(page, "07-resolved-queue");
}

async function main() {
  fs.mkdirSync("runs", { recursive: true });
  seedOpenIncident();

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  const page = await context.newPage();
  page.on("pageerror", (e) => report.pageErrors.push({ phase: CURRENT_PHASE, message: String(e.message || e) }));

  try {
    await login(page);
    CURRENT_PHASE = "baseline";
    const baseline = await fetchIncidentSummary(page);
    assert(baseline.status === 200, `baseline incidents -> 200 (got ${baseline.status})`);
    report.notes.push(`baseline active=${baseline.summary.active ?? "?"}`);

    await openIncidentsQueue(page);
    const row = await filterToProbe(page);
    const incidentId = row.id;
    report.incidentId = incidentId;

    await investigateFromQueue(page, incidentId);
    await escalateFromQueue(page, incidentId);
    await openDetailAndVerifyTimeline(page, incidentId);
    await resolveFromDetail(page, incidentId);
    await verifyResolvedInQueue(page, incidentId);

    assert(report.pageErrors.length === 0, `no uncaught page errors (${report.pageErrors.length})`);
    report.ok = true;
  } catch (err) {
    report.error = String(err?.message || err);
    await shot(page, "error");
  } finally {
    fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
    await browser.close();
  }

  const passed = report.asserts.filter((a) => a.pass).length;
  const failed = report.asserts.filter((a) => !a.pass).length;
  console.log(
    JSON.stringify(
      { ok: report.ok, passed, failed, steps: report.steps, incidentId: report.incidentId, error: report.error },
      null,
      2,
    ),
  );
  process.exit(report.ok ? 0 : 1);
}

main();
