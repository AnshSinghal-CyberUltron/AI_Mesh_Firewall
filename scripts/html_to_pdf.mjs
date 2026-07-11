/**
 * Convert an HTML file to PDF using Playwright (tests/e2e/node_modules).
 * Usage: node scripts/html_to_pdf.mjs <input.html> <output.pdf>
 */
import { chromium } from "playwright";
import { resolve } from "path";
import { fileURLToPath } from "url";

const [, , inputArg, outputArg] = process.argv;
if (!inputArg || !outputArg) {
  console.error("Usage: node scripts/html_to_pdf.mjs <input.html> <output.pdf>");
  process.exit(1);
}

const root = resolve(fileURLToPath(import.meta.url), "..", "..");
const input = resolve(root, inputArg);
const output = resolve(root, outputArg);

const browser = await chromium.launch();
const page = await browser.newPage();
await page.goto(`file:///${input.replace(/\\/g, "/")}`, { waitUntil: "networkidle" });
await page.pdf({
  path: output,
  format: "A4",
  printBackground: true,
  margin: { top: "12mm", right: "12mm", bottom: "14mm", left: "12mm" },
});
await browser.close();
console.log(`PDF written: ${output}`);
