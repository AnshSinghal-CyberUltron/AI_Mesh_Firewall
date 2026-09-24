// Cold-load /login from the production bundle (vite preview :4173) and record every request.
const { chromium } = await import("/home/contact_cyberultron_com/.npm/_npx/9833c18b2d85bc59/node_modules/playwright/index.js").then((m) => m.default || m);
const browser = await chromium.launch({ executablePath: "/opt/google/chrome/chrome", args: ["--no-sandbox"] });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
const reqs = [];
page.on("requestfinished", async (r) => {
  const s = await r.sizes().catch(() => null);
  reqs.push({ url: r.url().replace(/\?.*$/, "?…"), type: r.resourceType(), status: (await r.response())?.status(), respBodyBytes: s?.responseBodySize, respHeaderBytes: s?.responseHeadersSize });
});
page.on("requestfailed", (r) => reqs.push({ url: r.url(), type: r.resourceType(), failed: r.failure()?.errorText }));
const t0 = Date.now();
await page.goto("http://127.0.0.1:4173/login", { waitUntil: "networkidle", timeout: 30000 });
const t1 = Date.now();
const apiCalls = reqs.filter((r) => /\/api\/|\/v1\//.test(r.url));
console.log(JSON.stringify({ ms_to_networkidle: t1 - t0, total_requests: reqs.length, api_calls: apiCalls.length, requests: reqs }, null, 1));
await browser.close();
