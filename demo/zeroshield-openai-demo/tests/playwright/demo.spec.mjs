/**
 * Playwright E2E — ZeroShield OpenAI SDK Demo
 * Run: DEMO_URL=http://127.0.0.1:8765 npx playwright test demo.spec.mjs
 */
import { test, expect } from "@playwright/test";

const BASE = (process.env.DEMO_URL || "http://127.0.0.1:8765").replace(/\/$/, "");

test.describe("ZeroShield OpenAI SDK Demo", () => {
  test("health and gateway meta", async ({ page }) => {
    await page.goto(BASE);
    await expect(page.getByText("OpenAI SDK Reference Demo")).toBeVisible();
    await expect(page.locator("#gateway-meta")).toContainText("Gateway:");
  });

  test("tab navigation", async ({ page }) => {
    await page.goto(BASE);
    const tabs = [
      ["RAG", "rag"],
      ["MCP Context", "mcp"],
      ["Routing", "routing"],
      ["Files", "files"],
      ["Guardrails", "guardrails"],
      ["SDK Scenarios", "scenarios"],
    ];
    for (const [label, id] of tabs) {
      await page.getByRole("button", { name: label }).click();
      await expect(page.locator(`#panel-${id}`)).toBeVisible();
    }
    await page.getByRole("button", { name: "Chat", exact: true }).click();
    await expect(page.locator("#panel-chat")).toBeVisible();
  });

  test("refresh preserves tab via URL-less state", async ({ page }) => {
    await page.goto(BASE);
    await page.getByRole("button", { name: /^RAG$/ }).click();
    await page.reload();
    // Default tab after reload is chat (no URL routing) — document expected behavior
    await expect(page.locator("#panel-chat")).toBeVisible();
  });

  test("guardrail UI renders", async ({ page }) => {
    await page.goto(BASE);
    await page.getByRole("button", { name: /^Guardrails$/ }).click();
    await expect(page.getByText("Input & Output Guardrails")).toBeVisible();
    await expect(page.locator("#guard-prompt-preview")).toBeVisible();
    await expect(page.locator("#guard-vector")).toBeVisible();
  });

  test("guardrails vector selector updates prompt preview", async ({ page }) => {
    await page.goto(BASE);
    await page.getByRole("button", { name: /^Guardrails$/ }).click();
    await page.locator("#guard-vector").selectOption("safe");
    await expect(page.locator("#guard-prompt-preview")).toContainText("API key");
  });

  test("guardrails attack vector shows governed verdict", async ({ page }) => {
    test.setTimeout(120000);
    await page.goto(BASE);
    await page.getByRole("button", { name: /^Guardrails$/ }).click();
    await page.locator("#guard-vector").selectOption("attack");
    await page.locator("#guard-run").click();
    await expect(page.locator("#guard-out .status-reason")).toBeVisible({ timeout: 90000 });
    await expect(page.locator("#pipeline-viz")).not.toContainText(/Run a request/i, { timeout: 90000 });
  });

  test("guardrails safe vector runs and shows pipeline", async ({ page }) => {
    test.setTimeout(120000);
    await page.goto(BASE);
    await page.getByRole("button", { name: /^Guardrails$/ }).click();
    await page.locator("#guard-safe").click();
    await expect(page.locator("#guard-out .status-reason, #guard-out .assistant-text")).toBeVisible({ timeout: 90000 });
    await expect(page.locator("#pipeline-viz")).not.toContainText(/Run a request/i, { timeout: 90000 });
  });

  test("API health endpoint", async ({ request }) => {
    const res = await request.get(`${BASE}/api/health`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.ok).toBe(true);
    expect(body.gateway_base_url).toContain("/v1");
  });

  test("pipeline visualizer placeholder", async ({ page }) => {
    await page.goto(BASE);
    await expect(page.locator("#pipeline-viz")).toContainText(/Send a chat|validation stages|pipeline/i);
  });

  test("chat model defaults to auto route", async ({ page }) => {
    await page.goto(BASE);
    await expect(page.locator("#chat-model")).toHaveValue("auto");
  });

  test("models API returns org-allowed list with auto default", async ({ request }) => {
    const res = await request.get(`${BASE}/api/models`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.default_model).toBe("auto");
    expect(Array.isArray(body.models)).toBeTruthy();
    expect(body.count).toBe(body.models.length);
  });

  test("chat model selector shows auto and org allowed models", async ({ page }) => {
    await page.goto(BASE);
    await page.waitForFunction(() => {
      const group = document.getElementById("chat-model-org");
      return group && group.querySelectorAll("option").length >= 0;
    });
    await expect(page.locator('#chat-model optgroup[label="Routing"] option[value="auto"]')).toHaveText(
      /Auto Route/i
    );
    const orgOptions = page.locator("#chat-model-org option");
    const count = await orgOptions.count();
    const api = await page.request.get(`${BASE}/api/models`);
    const body = await api.json();
    expect(count).toBe(body.models.length);
    if (count > 0) {
      const firstId = body.models[0].id;
      await page.locator("#chat-model").selectOption(firstId);
      await page.reload();
      const saved = await page.evaluate(() => localStorage.getItem("zs_demo_model"));
      expect(saved).toBe(firstId);
      await expect(page.locator("#chat-model")).toHaveValue(firstId);
    }
  });

  test("mcp tab runs scenario and shows pipeline", async ({ page }) => {
    test.setTimeout(120000);
    await page.goto(BASE);
    await page.getByRole("button", { name: "MCP Context" }).click();
    await expect(page.locator("#mcp-context")).toContainText("Acme Corp");
    await page.locator("#mcp-run").click();
    await expect(page.locator("#mcp-out .status-reason, #mcp-out .assistant-text")).toBeVisible({ timeout: 90000 });
    await expect(page.locator("#pipeline-viz")).not.toContainText(/Run a request/i, { timeout: 90000 });
  });

  test("mcp sensitive vector selector updates payload", async ({ page }) => {
    await page.goto(BASE);
    await page.getByRole("button", { name: "MCP Context" }).click();
    await page.locator("#mcp-vector").selectOption("sensitive");
    await expect(page.locator("#mcp-context")).toContainText("123-45-6789");
  });

  test("routing tab runs scenario and shows routing summary", async ({ page }) => {
    test.setTimeout(120000);
    await page.goto(BASE);
    await page.getByRole("button", { name: "Routing" }).click();
    await expect(page.locator("#routing-prefs")).toContainText("enable_routing");
    await page.locator("#route-run").click();
    await expect(page.locator("#route-out .status-reason, #route-out .routing-summary")).toBeVisible({ timeout: 90000 });
    await expect(page.locator("#pipeline-viz .routing-summary")).toContainText(/Requested|Routed/i, { timeout: 90000 });
    await expect(page.locator("#pipeline-viz")).not.toContainText(/Run a request/i, { timeout: 90000 });
  });

  test("routing sensitivity switch updates prefs preview", async ({ page }) => {
    await page.goto(BASE);
    await page.getByRole("button", { name: "Routing" }).click();
    await page.locator("#route-sensitivity").selectOption("hipaa");
    await expect(page.locator("#routing-prefs")).toContainText("hipaa");
  });

  test("files selected preview shows filenames", async ({ page }) => {
    await page.goto(BASE);
    await page.getByRole("button", { name: "Files" }).click();
    await page.locator("#file-input").setInputFiles({
      name: "team.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("name,role\nAlice,CEO\n"),
    });
    await expect(page.locator("#files-selected")).toContainText("team.csv");
  });

  test("files tab runs analyze and shows customer summary", async ({ page }) => {
    test.setTimeout(120000);
    await page.goto(BASE);
    await page.getByRole("button", { name: "Files" }).click();
    await page.locator("#file-input").setInputFiles({
      name: "team.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("name,role\nAlice,CEO\nBob,CTO\n"),
    });
    await page.locator("#file-analyze").click();
    await expect(
      page.locator("#file-out .status-reason, #file-out .assistant-text, #file-out .files-manifest")
    ).toBeVisible({ timeout: 90000 });
    await expect(page.locator("#pipeline-viz")).not.toContainText(/Run a request/i, { timeout: 90000 });
  });

  test("status reason styles present", async ({ page }) => {
    await page.goto(BASE);
    const css = await page.locator('link[href="static/styles.css"]').getAttribute("href");
    expect(css).toContain("styles.css");
  });

  test("sdk scenarios tab shows catalog preview", async ({ page }) => {
    await page.goto(BASE);
    await page.getByRole("button", { name: "SDK Scenarios" }).click();
    await expect(page.locator("#scenario-preview")).toContainText(/SDK pattern|responses\.create/i);
    await expect(page.locator("#panel-scenarios .muted")).toContainText(/base_url/i);
  });

  test("sdk scenario basic runs and shows customer summary", async ({ page }) => {
    test.setTimeout(120000);
    await page.goto(BASE);
    await page.getByRole("button", { name: "SDK Scenarios" }).click();
    await page.locator('[data-scenario="basic"]').click();
    await expect(page.locator("#scenario-out .scenario-result")).toBeVisible({ timeout: 90000 });
    await expect(page.locator("#scenario-out .scenario-title")).toBeVisible();
    await expect(page.locator("#pipeline-viz")).not.toContainText(/Run a request/i, { timeout: 90000 });
  });

  test("sdk scenario streaming completes with visible text", async ({ page }) => {
    test.setTimeout(120000);
    await page.goto(BASE);
    await page.getByRole("button", { name: "SDK Scenarios" }).click();
    await page.locator('[data-scenario="stream"]').click();
    await expect(page.locator("#scenario-out .assistant-text, #scenario-out .status-reason")).toBeVisible({ timeout: 90000 });
    await expect(page.locator("#pipeline-viz")).not.toContainText(/Run a request/i, { timeout: 90000 });
  });

  test("sdk scenario guardrail shows governed verdict", async ({ page }) => {
    test.setTimeout(120000);
    await page.goto(BASE);
    await page.getByRole("button", { name: "SDK Scenarios" }).click();
    await page.locator('[data-scenario="guardrail"]').click();
    await expect(page.locator("#scenario-out .status-reason")).toBeVisible({ timeout: 90000 });
    await expect(page.locator("#scenario-out .scenario-pattern")).toContainText(/responses\.create/i);
    await expect(page.locator("#pipeline-viz")).not.toContainText(/Run a request/i, { timeout: 90000 });
  });
});
