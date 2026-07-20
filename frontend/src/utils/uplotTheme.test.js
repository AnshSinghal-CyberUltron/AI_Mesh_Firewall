import test from "node:test";
import assert from "node:assert/strict";
import {
  getUplotTheme,
  uplotLightTheme,
  uplotDarkTheme,
  UPLOT_FONT,
  UPLOT_THEMES,
} from "./uplotTheme.js";
import { CHART_PALETTE } from "./chartTheme.js";

function luminance(hex) {
  const m = /^#([0-9a-f]{6})$/i.exec(hex);
  if (!m) return null; // rgba() grid color → skip
  const n = parseInt(m[1], 16);
  const chan = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * chan[0] + 0.7152 * chan[1] + 0.0722 * chan[2];
}

test("getUplotTheme selects by isDark", () => {
  assert.equal(getUplotTheme(true), uplotDarkTheme);
  assert.equal(getUplotTheme(false), uplotLightTheme);
  assert.deepEqual(Object.keys(UPLOT_THEMES).sort(), ["dark", "light"]);
});

test("both themes expose the fields UPlotChart consumes", () => {
  for (const t of [uplotLightTheme, uplotDarkTheme]) {
    for (const k of ["axisStroke", "gridStroke", "tickStroke", "strokeWidth", "font", "palette"]) {
      assert.ok(k in t, `missing field ${k}`);
    }
    assert.equal(t.font, UPLOT_FONT);
    // series palette is shared with the ECharts charts (one visual system)
    assert.deepEqual(t.palette, CHART_PALETTE);
    assert.equal(t.strokeWidth, 1.5);
  }
});

test("axis color inverts between themes (no cross-wiring)", () => {
  // light axis = dark ink; dark axis = light ink
  assert.ok(luminance(uplotLightTheme.axisStroke) < 0.2, "light axis should be dark ink");
  assert.ok(luminance(uplotDarkTheme.axisStroke) > 0.3, "dark axis should be light ink");
});
