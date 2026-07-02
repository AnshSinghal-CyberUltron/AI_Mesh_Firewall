import { useEffect, useRef } from "react";
import ReactEChartsCore from "echarts-for-react/lib/core";
import echarts from "./echartsCore";
import { useTheme } from "../../context/ThemeContext";
import { getChartThemeName } from "../../utils/chartTheme";

/**
 * Theme-aware ECharts primitive for ZeroShield dashboards.
 *
 * - Selects the registered `zs-light` / `zs-dark` theme from `resolvedTheme`, so
 *   axes, grid, tooltip, legend and text all follow the active theme (fixes the
 *   old hardcoded-dark recharts styling that showed a dark grid on white).
 * - Container-size responsive: a ResizeObserver resizes the chart on layout
 *   changes that a plain window-resize listener misses (tab switches, collapsible
 *   sections, sidebar toggles) — the same robustness SafeResponsiveChart gave
 *   recharts.
 * - Honors `prefers-reduced-motion: reduce` by disabling chart animation.
 *
 * The wrapping element must have a real height (e.g. an `h-[220px]` parent), the
 * same contract charts had before.
 */
export function EChart({
  option,
  className,
  style,
  notMerge = true,
  lazyUpdate = true,
  onEvents,
  opts,
  ...rest
}) {
  const chartRef = useRef(null);
  const wrapRef = useRef(null);
  const themeCtx = typeof useTheme === "function" ? useTheme() : null;
  const isDark = themeCtx?.resolvedTheme === "dark";

  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    let raf = 0;
    const ro = new ResizeObserver(() => {
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        chartRef.current?.getEchartsInstance?.()?.resize();
      });
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);

  const reduceMotion =
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
  const finalOption = reduceMotion ? { ...option, animation: false } : option;

  return (
    <div ref={wrapRef} className={className} style={{ width: "100%", height: "100%", ...style }}>
      <ReactEChartsCore
        ref={chartRef}
        echarts={echarts}
        option={finalOption}
        theme={getChartThemeName(isDark)}
        notMerge={notMerge}
        lazyUpdate={lazyUpdate}
        style={{ width: "100%", height: "100%" }}
        opts={{ renderer: "canvas", ...opts }}
        onEvents={onEvents}
        {...rest}
      />
    </div>
  );
}

export default EChart;
