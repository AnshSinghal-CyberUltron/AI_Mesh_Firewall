/**
 * Rigorous frontend E2E — one pass per product area (9 modules).
 * Run: BASE_URL=http://127.0.0.1:8180 node scripts/playwright_nine_modules.mjs
 */
import { chromium } from "playwright";
import fs from "node:fs";

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8180").replace(/\/$/, "");
const EMAIL = process.env.TEST_EMAIL || "admin@zeroshield.io";
const PASS = process.env.TEST_PASSWORD || "Adm1n!Pass#2024";
const OUT = process.env.E2E_REPORT || "runs/playwright_nine_modules.json";

const MODULES = [
  {
    id: "overview",
    tab: "firewall",
    title: "AI Mesh Firewall",
    actions: async (page, ctx) => {
      await ctx.expectNoGatewayControlError(page);
      const refresh = page.getByRole("button", { name: /refresh/i }).first();
      if (await refresh.isVisible().catch(() => false)) await refresh.click();
    },
  },
  {
    id: "1.1",
    tab: "firewall-1-1",
    title: "AI Gateway & Traffic Ingress",
    actions: async (page, ctx) => {
      await ctx.expectGatewayHealth(page);
      await page.getByRole("button", { name: "Prompt Injection" }).click();
      const run = page.getByRole("button", { name: /run pipeline/i });
      if (!(await run.isEnabled())) {
        await page.locator("textarea").first().fill("Test probe for gateway pipeline.");
      }
      await run.click();
      await page.waitForTimeout(12000);
      const text = await page.locator("body").innerText();
      ctx.assert(!/Cannot reach gateway at http:\/\/control/i.test(text), "no control:8000 error");
      ctx.assert(!/Bedrock inference failed via boto3/i.test(text) || /credentials missing/i.test(text), "bedrock or clear config msg");
    },
  },
  {
    id: "1.2",
    tab: "firewall-1-2",
    title: "Policy Management",
    actions: async (page, ctx) => {
      await ctx.expectApiOk(page, "/api/policies/?policy_domain=global");
      const create = page.getByRole("button", { name: /create|new policy/i }).first();
      if (await create.isVisible().catch(() => false)) {
        ctx.notes.push("Create policy button visible");
      }
      const compile = page.getByRole("button", { name: /compile/i }).first();
      if (await compile.isVisible().catch(() => false)) {
        ctx.notes.push("Compile policies control visible");
      }
    },
  },
  {
    id: "1.3",
    tab: "firewall-1-3",
    title: "RAG & Vector DB Firewall",
    actions: async (page, ctx) => {
      await ctx.expectApiOk(page, "/api/vector-providers/");
      await ctx.expectApiOk(page, "/api/security/rag-pipeline-kpis/?period=24h");
      const runRag = page.getByRole("button", { name: /run|simulate|test/i }).first();
      if (await runRag.isVisible().catch(() => false)) {
        await runRag.click().catch(() => {});
        await page.waitForTimeout(5000);
        ctx.notes.push("RAG simulator action clicked");
      }
    },
  },
  {
    id: "1.4",
    tab: "firewall-1-4",
    title: "Context Assembly & MCP",
    actions: async (page, ctx) => {
      await ctx.expectApiOk(page, "/api/mcp-connector/servers/");
      const sim = page.getByRole("button", { name: /simulate|run|test route/i }).first();
      if (await sim.isVisible().catch(() => false)) {
        await sim.click().catch(() => {});
        await page.waitForTimeout(5000);
        ctx.notes.push("MCP simulator triggered");
      }
    },
  },
  {
    id: "1.5",
    tab: "firewall-1-5",
    title: "Multi-Model Governance",
    actions: async (page, ctx) => {
      await ctx.expectApiOk(page, "/api/models/status/");
      const route = page.getByRole("button", { name: /route|simulate|run/i }).first();
      if (await route.isVisible().catch(() => false)) {
        await route.click().catch(() => {});
        await page.waitForTimeout(5000);
        ctx.notes.push("Model routing simulator triggered");
      }
    },
  },
  {
    id: "1.6",
    tab: "firewall-1-6",
    title: "Model Isolation & Kill-Switch",
    actions: async (page, ctx) => {
      await ctx.expectApiOk(page, "/api/kill-switches/");
      const sim = page.getByRole("button", { name: /simulate|trip|test/i }).first();
      if (await sim.isVisible().catch(() => false)) {
        await sim.click().catch(() => {});
        await page.waitForTimeout(4000);
        ctx.notes.push("Circuit breaker simulator triggered");
      }
    },
  },
  {
    id: "1.7",
    tab: "firewall-1-7",
    title: "Output Guardrails",
    actions: async (page, ctx) => {
      const guard = page.getByRole("button", { name: /scan|guard|simulate|run/i }).first();
      if (await guard.isVisible().catch(() => false)) {
        await guard.click().catch(() => {});
        await page.waitForTimeout(5000);
        ctx.notes.push("Output guard simulator triggered");
      }
    },
  },
  {
    id: "inputs",
    tab: "firewall-config",
    title: "Inputs",
    actions: async (page, ctx) => {
      await ctx.expectApiOk(page, "/api/firewall/config/");
      const bedrockHealth = page.getByRole("button", { name: /check.*health|bedrock.*health/i }).first();
      if (await bedrockHealth.isVisible().catch(() => false)) {
        await bedrockHealth.click();
        await page.waitForTimeout(8000);
        const t = await page.locator("body").innerText();
        ctx.assert(!/Cannot reach gateway at http:\/\/control/i.test(t), "bedrock panel gateway URL ok");
        ctx.notes.push("Bedrock health check clicked");
      }
    },
  },
];

function makeCtx(token) {
  const notes = [];
  return {
    notes,
    assert(cond, msg) {
      if (!cond) throw new Error(msg);
    },
    async expectApiOk(page, path) {
      const res = await page.request.get(`${BASE}${path}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      this.assert(res.ok(), `API ${path} -> ${res.status()}`);
      notes.push(`API ${path} ${res.status()}`);
    },
    async expectGatewayHealth(page) {
      const res = await page.request.get(`${BASE}/health`);
      this.assert(res.ok(), `proxied gateway /health -> ${res.status()}`);
      notes.push(`gateway health ${res.status()}`);
    },
    async expectNoGatewayControlError(page) {
      const t = await page.locator("body").innerText();
      this.assert(!/Cannot reach gateway at http:\/\/control/i.test(t), "no internal docker gateway URL in UI");
    },
  };
}

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 120000 });
  await page.locator("#email").fill(EMAIL);
  await page.locator("#password").fill(PASS);
  const [res] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/auth/token/") && r.request().method() === "POST"),
    page.getByRole("button", { name: /^sign in$/i }).click(),
  ]);
  if (!res.ok()) throw new Error(`Login failed: ${res.status()}`);
  const data = await res.json();
  return data.access;
}

async function main() {
  fs.mkdirSync("runs", { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const report = { base: BASE, modules: [], failed: 0 };

  const token = await login(page);

  for (const mod of MODULES) {
    const entry = { id: mod.id, tab: mod.tab, title: mod.title, ok: true, notes: [], error: null };
    const ctx = makeCtx(token);
    try {
      await page.goto(`${BASE}/?tab=${mod.tab}`, { waitUntil: "networkidle", timeout: 120000 });
      const body = await page.locator("body").innerText();
      if (/Failed to fetch|Vite.*error|Cannot find module/i.test(body)) {
        throw new Error("Fatal UI error on page");
      }
      await mod.actions(page, ctx);
      entry.notes = ctx.notes;
      console.log("OK", mod.title, entry.notes.join("; ") || "(actions passed)");
    } catch (e) {
      entry.ok = false;
      entry.error = e.message;
      report.failed += 1;
      console.log("FAIL", mod.title, e.message);
    }
    report.modules.push(entry);
  }

  await browser.close();
  fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
  console.log("Report:", OUT);
  process.exit(report.failed ? 1 : 0);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
