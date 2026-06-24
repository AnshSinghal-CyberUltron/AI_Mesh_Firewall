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
    await expect(page.getByText("Output Validation Demo")).toBeVisible();
    await expect(page.locator("#guard-prompt")).toBeVisible();
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
    await expect(page.locator("#pipeline-viz")).toContainText(/Run a request|pipeline/i);
  });
});
