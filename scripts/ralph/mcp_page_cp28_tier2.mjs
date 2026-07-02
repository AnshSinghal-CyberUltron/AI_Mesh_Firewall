/**
 * MCP-page Ralph — CP28: the org Tier-2 SegmentedControl (Scan Controls tab) must
 * switch Inherit/Enabled/Disabled without a 400. Before the fix, saveMcpTier2 sent
 * the whole firewallConfig on PUT and a stale allowed_models 400'd the toggle; now
 * it sends only {mcp_tier2_enabled}. Asserts every PUT /api/firewall/config/ is 200
 * and the badge reflects the chosen state.
 */
import fs from "node:fs";
import { launchBrowser, login, BASE } from "./mcp_page_typesim.mjs";

const OUT = process.env.SHOT_DIR || "mcp-parallel/findings/mcp-page/cp28";

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const report = { checkpoint: "28", puts: [], steps: {}, ok: false };
  const { browser, page } = await launchBrowser();
  page.on("pageerror", (e) => (report.pageError = String(e.message || e)));
  // Record every firewall-config PUT status (the toggle's endpoint).
  page.on("response", (res) => {
    const u = res.url();
    if (u.includes("/api/firewall/config/") && res.request().method() === "PUT") {
      report.puts.push({ status: res.status() });
    }
  });

  const badgeText = async () => {
    // The Tier-2 card header badge (Inherit/Enabled/Disabled).
    const loc = page.getByText(/Tier-2 \(.*\) for this org/i).locator("xpath=..");
    return (await loc.innerText().catch(() => "")) || "";
  };

  async function clickSegment(name) {
    const seg = page.getByRole("button", { name: new RegExp(`^${name}$`, "i") })
      .or(page.getByRole("radio", { name: new RegExp(`^${name}$`, "i") }))
      .or(page.getByText(new RegExp(`^${name}$`), { exact: true }));
    const before = report.puts.length;
    await seg.first().click({ timeout: 8000 });
    // wait for the PUT to land
    for (let i = 0; i < 20; i++) { if (report.puts.length > before) break; await page.waitForTimeout(250); }
    await page.waitForTimeout(400);
    return report.puts[report.puts.length - 1];
  }

  try {
    await login(page);
    await page.goto(`${BASE}/?tab=firewall-1-4`, { waitUntil: "domcontentloaded", timeout: 120000 });
    await page.getByText("MCP Guardrails", { exact: false }).first().waitFor({ state: "visible", timeout: 60000 }).catch(() => {});
    // Scan Controls tab
    await page.getByRole("button", { name: /^Scan Controls$/i }).first().click({ timeout: 15000 }).catch(async () => {
      await page.getByText(/Scan Controls/i).first().click({ timeout: 15000 });
    });
    await page.getByText(/Tier-2 \(.*\) for this org/i).first().waitFor({ state: "visible", timeout: 20000 });

    for (const [label, expectBadge] of [["Enabled", /Enabled/i], ["Disabled", /Disabled/i], ["Inherit", /Inherit/i]]) {
      const put = await clickSegment(label);
      const badge = await badgeText();
      report.steps[label] = {
        putStatus: put?.status ?? null,
        badgeReflects: expectBadge.test(badge),
        no400: put ? put.status !== 400 : false,
      };
      await page.screenshot({ path: `${OUT}/${label.toLowerCase()}.png` }).catch(() => {});
    }

    const allPuts200 = report.puts.length >= 3 && report.puts.every((p) => p.status === 200);
    const allBadges = Object.values(report.steps).every((s) => s.badgeReflects);
    const no400 = report.puts.every((p) => p.status !== 400);
    report.cp28Pass = allPuts200 && allBadges && no400 && !report.pageError;
    report.ok = true;
    console.log(JSON.stringify({ puts: report.puts, steps: report.steps, allPuts200, allBadges, no400, cp28Pass: report.cp28Pass }, null, 2));
    console.log(report.cp28Pass ? "CP28: PASS — Tier-2 Inherit/Enabled/Disabled all 200, badge reflects, no 400" : "CP28: FAIL");
  } catch (e) {
    report.error = e.message;
    await page.screenshot({ path: `${OUT}/99-error.png` }).catch(() => {});
    console.error("CP28 FAIL:", e.message);
  } finally {
    fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2));
    await browser.close();
    process.exit(report.cp28Pass ? 0 : 1);
  }
}
main();
