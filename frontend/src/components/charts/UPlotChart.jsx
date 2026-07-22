import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { useTheme } from "../../context/ThemeContext";
import { getUplotTheme } from "../../utils/uplotTheme";
import { stackData } from "../../utils/uplotStack.js";

// hex (#rrggbb) + alpha → rgba().
function hexA(hex, a) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

// Compact axis numbers (5000→"5k", 1.5e6→"1.5M") so a fixed-width y-axis never
// clips 5-digit telemetry values. Exact values remain in the hover tooltip.
export function compactNum(v) {
  if (v == null || !Number.isFinite(v)) return "";
  const a = Math.abs(v);
  if (a >= 1e9) return +(v / 1e9).toFixed(1) + "B";
  if (a >= 1e6) return +(v / 1e6).toFixed(1) + "M";
  if (a >= 1e3) return +(v / 1e3).toFixed(1) + "k";
  return String(v);
}

/**
 * uPlot-backed chart for DENSE TIME-SERIES (telemetry/logs/trends). Canvas —
 * stays fast where recharts (SVG) degrades. Sized by the parent
 * (SafeResponsiveChart passes measured width/height, which uPlot requires).
 *
 * Theme-aware (light/dark). Non-sparkline charts get a cursor legend (values at
 * hover) + drag-to-zoom (x). Area series use a vertical gradient fill matching
 * the old recharts gradients.
 *
 * Props:
 *   data:    [ xValues[], ...ySeriesValues[] ]   (raw; x unix SECONDS when time)
 *   series:  [ { label, stroke?, fill?, width?, area? }, … ]  one per y-series
 *   width,height: pixels (from SafeResponsiveChart)
 *   time:    x is a time scale (default true; use false + xLabels for bucket labels)
 *   xLabels: string[] mapping x-index → axis/tooltip label (for bucketed series)
 *   stacked: cumulative stacked areas (tooltip shows raw per-series values)
 *   sparkline: hide axes/legend/cursor — compact trend glyph
 *   yRange:  optional uPlot y-scale range
 */
export function UPlotChart({
  data,
  series = [],
  width,
  height,
  time = true,
  sparkline = false,
  stacked = false,
  yRange,
  xLabels,
  className,
  ...optsOverride
}) {
  const elRef = useRef(null);
  const uRef = useRef(null);
  // Refs so axis/tooltip formatters always read the latest data/labels (polling
  // updates the arrays without rebuilding the chart).
  const rawRef = useRef(data);
  const labelsRef = useRef(xLabels);
  rawRef.current = data;
  labelsRef.current = xLabels;

  const themeCtx = typeof useTheme === "function" ? useTheme() : null;
  const isDark = themeCtx?.resolvedTheme === "dark";
  // Rebuild only on structural / theme change; size & data patch in place.
  const seriesKey = JSON.stringify([
    series.map((s) => [s.label, s.stroke, !!s.area, s.width]),
    stacked,
    sparkline,
    time,
    !!xLabels,
  ]);

  useEffect(() => {
    const el = elRef.current;
    if (!el || !width || !height) return undefined;
    const t = getUplotTheme(isDark);

    const gradientFill = (color) => (u) => {
      const solid = hexA(color, isDark ? 0.22 : 0.16);
      const bbox = u && u.bbox;
      // uPlot may resolve the fill before a valid bbox exists; guard against the
      // non-finite values that would make createLinearGradient throw (and crash
      // the whole chart/render). Fall back to a flat translucent fill.
      const top = bbox && Number.isFinite(bbox.top) ? bbox.top : 0;
      const h = bbox && Number.isFinite(bbox.height) ? bbox.height : 0;
      if (!u || !u.ctx || h <= 0) return solid;
      try {
        const g = u.ctx.createLinearGradient(0, top, 0, top + h);
        g.addColorStop(0, hexA(color, isDark ? 0.5 : 0.4));
        g.addColorStop(1, hexA(color, 0.04));
        return g;
      } catch {
        return solid;
      }
    };

    const ySeries = series.map((s, i) => {
      const color = s.stroke || t.palette[i % t.palette.length];
      const cfg = {
        label: s.label || `series ${i + 1}`,
        stroke: color,
        width: s.width ?? t.strokeWidth,
        fill: s.area ? s.fill || gradientFill(color) : undefined,
        points: { show: false },
      };
      if (stacked) {
        // legend/tooltip shows the RAW value, not the cumulative plotted value
        cfg.value = (u, _v, seriesIdx, idx) => {
          const raw = rawRef.current;
          if (idx == null || !raw || !raw[seriesIdx]) return _v ?? "";
          return raw[seriesIdx][idx];
        };
      }
      return cfg;
    });

    const axis = {
      stroke: t.axisStroke,
      grid: { stroke: t.gridStroke, width: 1 },
      ticks: { stroke: t.tickStroke, width: 1 },
      font: t.font,
    };
    const xAxis = {
      ...axis,
      values: xLabels
        ? (u, splits) => splits.map((iv) => labelsRef.current?.[Math.round(iv)] ?? "")
        : undefined,
    };
    const xSeries = xLabels
      ? { label: "Time", value: (u, _v, _si, idx) => (idx == null ? "" : labelsRef.current?.[idx] ?? "") }
      : {};

    let plotData = rawRef.current && rawRef.current.length ? rawRef.current : [[]];
    let bands;
    if (stacked && plotData.length > 1) {
      const st = stackData(plotData);
      plotData = st.data;
      bands = st.bands;
    }

    const base = {
      width,
      height,
      scales: { x: { time }, y: yRange ? { range: yRange } : {} },
      series: [xSeries, ...ySeries],
      bands,
    };

    const opts = sparkline
      ? {
          ...base,
          axes: [{ show: false }, { show: false }],
          legend: { show: false },
          cursor: { show: false },
          padding: [2, 2, 2, 2],
          ...optsOverride,
        }
      : {
          ...base,
          axes: [xAxis, { ...axis, size: 46, values: (u, splits) => splits.map(compactNum) }],
          legend: { show: true },
          cursor: { drag: { x: true, y: false } },
          ...optsOverride,
        };

    const u = new uPlot(opts, plotData, el);
    uRef.current = u;
    return () => {
      u.destroy();
      uRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDark, seriesKey]);

  // Patch size in place.
  useEffect(() => {
    if (uRef.current && width && height) uRef.current.setSize({ width, height });
  }, [width, height]);

  // Patch data in place (re-stack if needed).
  useEffect(() => {
    if (!uRef.current || !data || !data.length) return;
    const pd = stacked && data.length > 1 ? stackData(data).data : data;
    uRef.current.setData(pd);
  }, [data, stacked]);

  return <div ref={elRef} className={className} />;
}

export default UPlotChart;
