// Theme-aware styling for uPlot dense time-series charts. Mirrors chartTheme.js
// (ECharts) so uPlot and ECharts charts read as one system: same series palette,
// same light/dark axis/grid colors derived from DESIGN.md tokens. Pure data +
// helpers → unit-testable (uplotTheme.test.js).
import { CHART_PALETTE } from "./chartTheme.js";

export const UPLOT_FONT =
  "11px 'Inter','Plus Jakarta Sans',-apple-system,BlinkMacSystemFont,'Segoe UI','Roboto',sans-serif";

function buildTheme(c) {
  return {
    axisStroke: c.axisStroke, // axis value/label color
    gridStroke: c.gridStroke, // grid line color
    tickStroke: c.tickStroke, // tick mark color
    strokeWidth: 1.5,
    font: UPLOT_FONT,
    palette: CHART_PALETTE,
  };
}

// Light: readable on the near-white app bg (axis slate-600 clears AA); grid slate-200.
export const uplotLightTheme = buildTheme({
  axisStroke: "#475569", // slate-600
  gridStroke: "#e2e8f0", // slate-200
  tickStroke: "#cbd5e1", // slate-300
});

// Dark: slate ramp on the slate-950 app bg.
export const uplotDarkTheme = buildTheme({
  axisStroke: "#94a3b8", // slate-400
  gridStroke: "rgba(148,163,184,0.16)",
  tickStroke: "#334155", // slate-700
});

export function getUplotTheme(isDark) {
  return isDark ? uplotDarkTheme : uplotLightTheme;
}

export const UPLOT_THEMES = { light: uplotLightTheme, dark: uplotDarkTheme };
