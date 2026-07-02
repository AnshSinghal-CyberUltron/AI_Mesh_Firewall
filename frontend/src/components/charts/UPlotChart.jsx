import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { useTheme } from "../../context/ThemeContext";
import { getUplotTheme } from "../../utils/uplotTheme";

// hex (#rrggbb) + alpha → rgba(), for area fills.
function hexA(hex, a) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

/**
 * uPlot-backed chart for DENSE TIME-SERIES (telemetry/logs/trends). uPlot renders
 * to canvas and stays fast at hundreds-to-thousands of points where recharts (SVG)
 * degrades. Sized by the parent — SafeResponsiveChart measures the container and
 * passes width/height — which is exactly what uPlot needs (it does not self-size).
 *
 * Theme-aware (light/dark axis+grid colors), built-in cursor legend + drag-to-zoom
 * (x). Reduced-motion is a non-issue: uPlot has no entrance animation.
 *
 * Props:
 *   data:    [ xValues[], ...ySeriesValues[] ]   x in unix SECONDS when time
 *   series:  [ { label, stroke?, fill?, width?, area? }, ... ]  one per y-series
 *   width,height: pixels (from SafeResponsiveChart)
 *   time:    x is a time scale (default true)
 *   sparkline: hide axes/legend/cursor — a compact trend glyph
 *   yRange:  optional uPlot y-scale range ([min,max] or fn)
 */
export function UPlotChart({
  data,
  series = [],
  width,
  height,
  time = true,
  sparkline = false,
  yRange,
  className,
  ...optsOverride
}) {
  const elRef = useRef(null);
  const uRef = useRef(null);
  const themeCtx = typeof useTheme === "function" ? useTheme() : null;
  const isDark = themeCtx?.resolvedTheme === "dark";
  // Rebuild only when structure/theme changes; data & size are patched in place.
  const seriesKey = JSON.stringify(series.map((s) => [s.label, s.stroke, !!s.area]));

  useEffect(() => {
    const el = elRef.current;
    if (!el || !width || !height) return undefined;
    const t = getUplotTheme(isDark);

    const ySeries = series.map((s, i) => {
      const color = s.stroke || t.palette[i % t.palette.length];
      return {
        label: s.label || `series ${i + 1}`,
        stroke: color,
        width: s.width ?? t.strokeWidth,
        fill: s.area ? s.fill || hexA(color, isDark ? 0.22 : 0.16) : undefined,
        points: { show: false },
      };
    });

    const axis = {
      stroke: t.axisStroke,
      grid: { stroke: t.gridStroke, width: 1 },
      ticks: { stroke: t.tickStroke, width: 1 },
      font: t.font,
    };

    const base = {
      width,
      height,
      scales: { x: { time }, y: yRange ? { range: yRange } : {} },
      series: [{}, ...ySeries],
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
          axes: [{ ...axis }, { ...axis, size: 46 }],
          legend: { show: true },
          cursor: { drag: { x: true, y: false } },
          ...optsOverride,
        };

    const u = new uPlot(opts, data && data.length ? data : [[]], el);
    uRef.current = u;
    return () => {
      u.destroy();
      uRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDark, seriesKey, sparkline, time]);

  // Patch size in place on layout change (no full rebuild).
  useEffect(() => {
    if (uRef.current && width && height) uRef.current.setSize({ width, height });
  }, [width, height]);

  // Patch data in place on refresh (no full rebuild).
  useEffect(() => {
    if (uRef.current && data && data.length) uRef.current.setData(data);
  }, [data]);

  return <div ref={elRef} className={className} />;
}

export default UPlotChart;
