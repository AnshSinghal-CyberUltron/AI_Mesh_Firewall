// Theme-aware ECharts themes for ZeroShield charts.
//
// These replace the old hardcoded-dark recharts styling (tooltip #0f172a, grid
// #3f3f46, ticks #94a3b8) that rendered a dark grid on white in light mode. Both
// themes are registered on the slim echarts core (see components/charts/echartsCore.js)
// and selected per resolvedTheme by <EChart>. Pure data + helpers → unit-testable
// (chartTheme.test.js). Colors mirror DESIGN.md tokens as concrete canvas colors.

export const CHART_FONT =
  "'Inter','Plus Jakarta Sans',-apple-system,BlinkMacSystemFont,'Segoe UI','Roboto',sans-serif";

// Categorical series palette — brand-aligned (indigo, teal, violet, amber,
// emerald, sky, rose, slate). Used as the default `color` cycle; panels with
// semantic colors (e.g. output-guardrail block/redact) pass their own itemStyle.
export const CHART_PALETTE = [
  "#6366f1", // indigo-500 (primary)
  "#14b8a6", // teal-500
  "#8b5cf6", // violet-500
  "#f59e0b", // amber-500
  "#10b981", // emerald-500
  "#0ea5e9", // sky-500
  "#f43f5e", // rose-500
  "#94a3b8", // slate-400
];

function buildTheme(c) {
  const axisCommon = {
    axisLine: { show: true, lineStyle: { color: c.axisLine } },
    axisTick: { show: false },
    axisLabel: { color: c.subtext, fontFamily: CHART_FONT, fontSize: 11 },
    splitLine: { show: true, lineStyle: { color: c.splitLine, type: "dashed" } },
    nameTextStyle: { color: c.subtext, fontFamily: CHART_FONT },
  };
  return {
    color: CHART_PALETTE,
    backgroundColor: "transparent",
    textStyle: { fontFamily: CHART_FONT, color: c.text },
    title: {
      textStyle: { color: c.text, fontFamily: CHART_FONT },
      subtextStyle: { color: c.subtext, fontFamily: CHART_FONT },
    },
    // categoryAxis draws no split lines by default (matches recharts vertical={false})
    categoryAxis: { ...axisCommon, splitLine: { show: false, lineStyle: { color: c.splitLine } } },
    valueAxis: axisCommon,
    logAxis: axisCommon,
    timeAxis: axisCommon,
    legend: {
      textStyle: { color: c.subtext, fontFamily: CHART_FONT, fontSize: 11 },
      inactiveColor: c.axisLine,
      icon: "roundRect",
      itemWidth: 10,
      itemHeight: 10,
    },
    tooltip: {
      backgroundColor: c.tooltipBg,
      borderColor: c.tooltipBorder,
      borderWidth: 1,
      textStyle: { color: c.tooltipText, fontFamily: CHART_FONT, fontSize: 12 },
      extraCssText: `border-radius:10px;box-shadow:${c.tooltipShadow};`,
      axisPointer: {
        lineStyle: { color: c.axisLine },
        crossStyle: { color: c.axisLine },
        shadowStyle: { color: c.cursorFill },
      },
    },
    radar: {
      // ECharts 6: axis label config is `axisName` (was `name.textStyle` pre-4.0)
      axisName: { color: c.subtext, fontFamily: CHART_FONT, fontSize: 11 },
      axisLine: { lineStyle: { color: c.splitLine } },
      splitLine: { lineStyle: { color: c.splitLine } },
      splitArea: { areaStyle: { color: ["transparent"] } },
    },
  };
}

// Light: readable on the near-white app bg. subtext slate-600 (#475569) clears
// AA on white (~7:1); grid slate-200; tooltip is light (white) — the opposite of
// the old hardcoded dark tooltip that looked out of place in light mode.
export const zsLightTheme = buildTheme({
  text: "#1e293b", // slate-800
  subtext: "#475569", // slate-600
  axisLine: "#cbd5e1", // slate-300
  splitLine: "#e2e8f0", // slate-200
  tooltipBg: "#ffffff",
  tooltipBorder: "#e2e8f0",
  tooltipText: "#1e293b",
  tooltipShadow: "0 8px 24px rgba(15,23,42,0.10)",
  cursorFill: "rgba(148,163,184,0.14)",
});

// Dark: slate ramp on the slate-950 app bg. tooltip #0f172a matches the previous
// recharts TOOLTIP_STYLE so dark charts stay visually identical (data-identity).
export const zsDarkTheme = buildTheme({
  text: "#e2e8f0", // slate-200
  subtext: "#94a3b8", // slate-400
  axisLine: "#334155", // slate-700
  splitLine: "rgba(148,163,184,0.16)",
  tooltipBg: "#0f172a", // slate-900
  tooltipBorder: "#334155", // slate-700
  tooltipText: "#e2e8f0",
  tooltipShadow: "0 12px 32px rgba(2,6,23,0.45)",
  cursorFill: "rgba(148,163,184,0.10)",
});

export function getChartThemeName(isDark) {
  return isDark ? "zs-dark" : "zs-light";
}

export const ZS_CHART_THEMES = { "zs-light": zsLightTheme, "zs-dark": zsDarkTheme };
