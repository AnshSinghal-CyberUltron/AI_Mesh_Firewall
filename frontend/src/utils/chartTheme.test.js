import test from "node:test";
import assert from "node:assert/strict";
import {
  CHART_PALETTE,
  zsLightTheme,
  zsDarkTheme,
  getChartThemeName,
  ZS_CHART_THEMES,
} from "./chartTheme.js";

// sRGB relative luminance of a #rrggbb color (WCAG-style), for inversion checks.
function luminance(hex) {
  const m = /^#([0-9a-f]{6})$/i.exec(hex);
  assert.ok(m, `expected #rrggbb, got ${hex}`);
  const n = parseInt(m[1], 16);
  const chan = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * chan[0] + 0.7152 * chan[1] + 0.0722 * chan[2];
}

test("getChartThemeName maps isDark → registered theme name", () => {
  assert.equal(getChartThemeName(true), "zs-dark");
  assert.equal(getChartThemeName(false), "zs-light");
  assert.deepEqual(Object.keys(ZS_CHART_THEMES).sort(), ["zs-dark", "zs-light"]);
});

test("categorical palette is 8 valid hex colors, no duplicates", () => {
  assert.equal(CHART_PALETTE.length, 8);
  for (const c of CHART_PALETTE) assert.match(c, /^#[0-9a-f]{6}$/i);
  assert.equal(new Set(CHART_PALETTE).size, CHART_PALETTE.length);
  // primary series is the brand indigo
  assert.equal(CHART_PALETTE[0], "#6366f1");
});

test("both themes expose the ECharts theme keys panels rely on", () => {
  for (const theme of [zsLightTheme, zsDarkTheme]) {
    for (const key of [
      "color",
      "backgroundColor",
      "textStyle",
      "categoryAxis",
      "valueAxis",
      "legend",
      "tooltip",
      "radar",
    ]) {
      assert.ok(key in theme, `missing theme key: ${key}`);
    }
    assert.equal(theme.backgroundColor, "transparent");
    assert.deepEqual(theme.color, CHART_PALETTE);
    // categoryAxis must not draw split lines (matches recharts vertical={false})
    assert.equal(theme.categoryAxis.splitLine.show, false);
    // valueAxis draws dashed split lines
    assert.equal(theme.valueAxis.splitLine.show, true);
    assert.equal(theme.valueAxis.splitLine.lineStyle.type, "dashed");
  }
});

test("light and dark are true inversions (no theme cross-wiring)", () => {
  // Primary text: dark ink in light theme, light ink in dark theme.
  assert.ok(luminance(zsLightTheme.textStyle.color) < 0.3, "light text should be dark ink");
  assert.ok(luminance(zsDarkTheme.textStyle.color) > 0.6, "dark text should be light ink");
  // Tooltip surface flips: white in light, slate-900 in dark (the old bug was a
  // hardcoded dark tooltip in both).
  assert.equal(zsLightTheme.tooltip.backgroundColor, "#ffffff");
  assert.equal(zsDarkTheme.tooltip.backgroundColor, "#0f172a");
  assert.ok(luminance(zsLightTheme.tooltip.textStyle.color) < 0.3);
  assert.ok(luminance(zsDarkTheme.tooltip.textStyle.color) > 0.6);
});

test("axis label contrast direction is correct per theme", () => {
  // light: dark-ish labels on light bg; dark: light-ish labels on dark bg
  assert.ok(luminance(zsLightTheme.valueAxis.axisLabel.color) < 0.2);
  assert.ok(luminance(zsDarkTheme.valueAxis.axisLabel.color) > 0.3);
});
