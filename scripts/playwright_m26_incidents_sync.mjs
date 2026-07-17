/**
 * Incident and Forensics — KPI load, filter, escalate, detail timeline, resolve sync gate.
 *
 * Run (host with playwright):
 *   NODE_PATH="$PWD/tests/e2e/node_modules" BASE_URL=http://127.0.0.1:8180 \
 *     node scripts/playwright_m26_incidents_sync.mjs
 *
 * Docker-only (no host docker CLI; stack on host :8180):
 *   docker run --rm --add-host=host.docker.internal:host-gateway -v "%CD%:/work" -w /work \
 *     mcr.microsoft.com/playwright:v1.60.0-jammy bash -lc \
 *     "cd tests/e2e && npm ci --omit=dev 2>/dev/null || npm install && \
 *      NODE_PATH=/work/tests/e2e/node_modules BASE_URL=http://host.docker.internal:8180 \
 *      M26_PROBE_STAMP=\$M26_PROBE_STAMP node /work/scripts/playwright_m26_incidents_sync.mjs"
 *
 * Pre-seed on host (optional): M26_INCIDENT_ID=<id> node scripts/playwright_m26_incidents_sync.mjs
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
const PROBE_TITLE = process.env.M26_PROBE_TITLE || `Incidents E2E probe ${STAMP}`;
const BULK_TITLE_A = `Incidents E2E bulk A ${STAMP}`;
const BULK_TITLE_B = `Incidents E2E bulk B ${STAMP}`;
const PRESEED_ID = process.env.M26_INCIDENT_ID ? Number(process.env.M26_INCIDENT_ID) : null;
const CONTROL_CONTAINER = process.env.CONTROL_CONTAINER || "ai_mesh_firewall-control-1";

const report = {
  base: BASE,
  ok: false,
  steps: [],
  asserts: [],
  pageErrors: [],
  notes: [],
  probeTitle: PROBE_TITLE,
  incidentId: null,
  bulkIds: [],
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

function tryDockerSeed() {
  const py =
    `from auth.models import Organization; from policy.models import EnforcementEvent, SecurityIncident; ` +
    `from policy.constants import ACTION_BLOCK; title=${JSON.stringify(PROBE_TITLE)}; ` +
    `bulk_a=${JSON.stringify(BULK_TITLE_A)}; bulk_b=${JSON.stringify(BULK_TITLE_B)}; ` +
    `org=Organization.objects.filter(slug=${JSON.stringify(ORG_SLUG)}).first(); assert org; ` +
    `ev=EnforcementEvent.objects.create(organization=org, action=ACTION_BLOCK, metadata={` +
    `'source':'threat_intel','threat_type':'prompt_injection','detail':title,'model':'gpt-4o','key_prefix':'zs_incidents'}); ` +
    `inc=SecurityIncident.objects.create(organization=org, enforcement_event=ev, title=title, severity='high', status='open'); ` +
    `ev_a=EnforcementEvent.objects.create(organization=org, action=ACTION_BLOCK, metadata={` +
    `'source':'threat_intel','threat_type':'prompt_injection','detail':bulk_a,'model':'gpt-4o','key_prefix':'zs_incidents'}); ` +
    `inc_a=SecurityIncident.objects.create(organization=org, enforcement_event=ev_a, title=bulk_a, severity='high', status='open'); ` +
    `ev_b=EnforcementEvent.objects.create(organization=org, action=ACTION_BLOCK, metadata={` +
    `'source':'threat_intel','threat_type':'prompt_injection','detail':bulk_b,'model':'gpt-4o','key_prefix':'zs_incidents'}); ` +
    `inc_b=SecurityIncident.objects.create(organization=org, enforcement_event=ev_b, title=bulk_b, severity='medium', status='open'); ` +
    `print(f"{inc.id},{inc_a.id},{inc_b.id}")`;
  try {
    const cmd =
      `docker exec -w /app/control ${CONTROL_CONTAINER} python manage.py shell -c ` + JSON.stringify(py);
    const out = execSync(cmd, { encoding: "utf-8", timeout: 90000 });
    const idLine = (out || "")
      .split(/\r?\n/)
      .map((l) => l.trim())
      .reverse()
      .find((l) => /^\d+,\d+,\d+$/.test(l));
    if (!idLine) throw new Error(`no seeded ids in: ${out}`);
    const [probeId, bulkAId, bulkBId] = idLine.split(",").map((v) => Number(v));
    report.incidentId = probeId;
    report.bulkIds = [bulkAId, bulkBId];
    report.notes.push(`seeded probe=${report.incidentId} bulk=${report.bulkIds.join(",")} (host docker)`);
    return true;
  } catch (err) {
    report.notes.push(`host docker seed skipped (${String(err?.message || err).slice(0, 140)})`);
    return false;
  }
}

function seedPreseedOrDocker() {
  if (PRESEED_ID) {
    report.incidentId = PRESEED_ID;
    report.notes.push(`using pre-seeded incident id=${PRESEED_ID}`);
    return;
  }
  tryDockerSeed();
}

async function seedOpenIncidentViaApi(page) {
  if (report.incidentId) return true;
  const data = await page.evaluate(
    async ({ probeTitle, bulkA, bulkB }) => {
      const tok = localStorage.getItem("auth_access");
      const r = await fetch("/api/module2/incidents/e2e-seed/", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${tok}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          probe_title: probeTitle,
          bulk_titles: [bulkA, bulkB],
        }),
      });
      const body = await r.json().catch(() => ({}));
      return { status: r.status, body };
    },
    { probeTitle: PROBE_TITLE, bulkA: BULK_TITLE_A, bulkB: BULK_TITLE_B },
  );
  if (data.status === 201 && data.body?.probe_id) {
    report.incidentId = data.body.probe_id;
    report.bulkIds = data.body.bulk_ids || [];
    report.notes.push(`seeded probe=${report.incidentId} bulk=${report.bulkIds.join(",")} (API)`);
    return true;
  }
  report.notes.push(
    `API seed unavailable (HTTP ${data.status}${data.body?.detail ? `: ${data.body.detail}` : ""})`,
  );
  return false;
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
  await page.getByRole("heading", { name: /Incident and Forensics/i }).waitFor({ state: "visible", timeout: 60000 });
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
    const data = await page.evaluate(
      async ({ incidentId }) => {
        const tok = localStorage.getItem("auth_access");
        const detailResp = await fetch(`/api/module2/incidents/${incidentId}/`, {
          headers: { Authorization: `Bearer ${tok}` },
        });
        const detail = await detailResp.json().catch(() => ({}));
        const title = detail?.incident?.title || "";
        const r = await fetch(`/api/module2/incidents/?search=${encodeURIComponent(title)}`, {
          headers: { Authorization: `Bearer ${tok}` },
        });
        const body = await r.json().catch(() => ({}));
        const row = (body.results || []).find((item) => item.id === incidentId) || (body.results || [])[0];
        return { status: r.status, row, title };
      },
      { incidentId: PRESEED_ID },
    );
    assert(data.row, `pre-seeded incident visible in API search (id=${PRESEED_ID})`);
    report.incidentId = data.row.id;
    report.probeTitle = data.title || report.probeTitle;
    await page.getByPlaceholder(/Search title or notes/i).fill(data.title || "");
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

async function viewFromQueue(page, incidentId) {
  CURRENT_PHASE = "m26-view";
  const row = page.locator("tr").filter({ has: page.getByRole("link", { name: new RegExp(`#${incidentId}`) }) }).first();
  await row.getByRole("link", { name: /^View$/i }).click();
  await page.waitForURL(new RegExp(`/incidents/${incidentId}`), { timeout: 60000 });
  report.steps.push("view-queue");
  await shot(page, "03-view");
  await page.getByRole("link", { name: /Back to Incidents/i }).click();
  await page.waitForURL(/\/incidents/, { timeout: 30000 });
}

async function escalateFromQueue(page, incidentId) {
  CURRENT_PHASE = "m26-escalate";
  const row = page.locator("tr").filter({ has: page.getByRole("link", { name: new RegExp(`#${incidentId}`) }) }).first();
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
  assert(Boolean(detail.source), `detail source is populated (got ${detail.source})`);

  await page.getByText(/Enforcement Timeline|Timeline/i).first().waitFor({ state: "visible", timeout: 30000 });
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
  await page.getByRole("link", { name: /Back to Incidents/i }).click();
  await page.waitForURL(/\/incidents/, { timeout: 30000 });
  await page.getByRole("heading", { name: /Incident and Forensics/i }).waitFor({ state: "visible", timeout: 60000 });

  await page.locator("select").filter({ hasText: /All statuses/i }).first().selectOption("resolved");
  await page.getByPlaceholder(/Search title or notes/i).fill(report.probeTitle || PROBE_TITLE);
  await page.getByPlaceholder(/Search title or notes/i).press("Enter");

  await page.getByRole("link", { name: new RegExp(`#${incidentId}`) }).waitFor({ state: "visible", timeout: 45000 });
  await page.getByText(/resolved/i).first().waitFor({ state: "visible", timeout: 30000 });
  report.steps.push("resolved-filter");
  await shot(page, "07-resolved-queue");
}

async function bulkResolveFromQueue(page) {
  CURRENT_PHASE = "m26-bulk-resolve";
  await page.goto(`${BASE}/incidents`, { waitUntil: "domcontentloaded", timeout: 120000 });
  await page.getByRole("heading", { name: /Incident and Forensics/i }).waitFor({ state: "visible", timeout: 60000 });
  if (!PRESEED_ID) {
    await page.getByPlaceholder(/Search title or notes/i).fill("Incidents E2E bulk");
    await page.getByPlaceholder(/Search title or notes/i).press("Enter");
    await page.waitForResponse(
      (r) => r.url().includes("/api/module2/incidents/") && r.url().includes("search=") && r.ok(),
      { timeout: 60000 },
    );
  }

  const firstCheckbox = page.getByRole("checkbox", { name: /Select incident/i }).first();
  const secondCheckbox = page.getByRole("checkbox", { name: /Select incident/i }).nth(1);
  await firstCheckbox.waitFor({ state: "visible", timeout: 30000 });
  await secondCheckbox.waitFor({ state: "visible", timeout: 30000 });
  await firstCheckbox.check();
  await secondCheckbox.check();

  page.once("dialog", (dialog) => dialog.accept());
  const [bulkResp] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes("/api/module2/incidents/bulk-resolve/") && r.request().method() === "POST" && r.ok(),
      { timeout: 60000 },
    ),
    page.getByRole("button", { name: /Resolve selected \(2\)/i }).click(),
  ]);
  const bulkBody = await bulkResp.json().catch(() => ({}));
  assert((bulkBody.resolved_count ?? 0) >= 2, `bulk resolve resolved >=2 incidents (got ${bulkBody.resolved_count})`);
  await page.getByText(/Resolved .* incident\(s\)/i).waitFor({ state: "visible", timeout: 30000 });
  report.steps.push("bulk-resolve");
  await shot(page, "08-bulk-resolved");
}

async function main() {
  fs.mkdirSync("runs", { recursive: true });
  seedPreseedOrDocker();

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  const page = await context.newPage();
  page.on("pageerror", (e) => report.pageErrors.push({ phase: CURRENT_PHASE, message: String(e.message || e) }));

  try {
    await login(page);
    if (!report.incidentId) {
      const seeded = await seedOpenIncidentViaApi(page);
      assert(seeded, "probe incidents seeded (host docker or API e2e-seed)");
    }
    CURRENT_PHASE = "baseline";
    const baseline = await fetchIncidentSummary(page);
    assert(baseline.status === 200, `baseline incidents -> 200 (got ${baseline.status})`);
    report.notes.push(`baseline active=${baseline.summary.active ?? "?"}`);

    await openIncidentsQueue(page);
    const row = await filterToProbe(page);
    const incidentId = row.id;
    report.incidentId = incidentId;

    await viewFromQueue(page, incidentId);
    await escalateFromQueue(page, incidentId);
    await openDetailAndVerifyTimeline(page, incidentId);
    await resolveFromDetail(page, incidentId);
    await verifyResolvedInQueue(page, incidentId);
    await bulkResolveFromQueue(page);

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
