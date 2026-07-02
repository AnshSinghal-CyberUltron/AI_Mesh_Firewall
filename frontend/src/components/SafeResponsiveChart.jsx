import { useEffect, useRef, useState } from "react";
import { ResponsiveContainer } from "recharts";
import { EChart } from "./charts/EChart";
import { UPlotChart } from "./charts/UPlotChart";

/**
 * ResizeObserver-gated chart wrapper.
 *
 * Three modes, one API (backward compatible):
 *  - **ECharts (preferred, interactive dashboards):** pass an `option` prop →
 *    renders the theme-aware <EChart> once the wrapper has a real measured size
 *    (FRONTEND_AUDIT.md R1); extra props forward to EChart.
 *  - **uPlot (dense time-series):** pass a `uplot` prop object ({data, series, …})
 *    → renders <UPlotChart> with the MEASURED pixel size (uPlot needs explicit
 *    width/height, which this wrapper already computes).
 *  - **recharts (legacy):** pass recharts JSX as `children` → renders the original
 *    <ResponsiveContainer> path unchanged, so un-migrated panels keep working.
 *
 * recharts measures its parent synchronously on mount; when a chart is mounted
 * inside a just-expanded collapsible section (or any container that is 0×0 for
 * a frame), it logs "The width(-1) and height(-1) of chart should be greater
 * than 0". The gate below only renders once the wrapping div has a real measured
 * size, eliminating that warning for both paths.
 *
 * The wrapper div must carry an explicit height (via `className`, e.g. h-[200px]),
 * since the inner chart fills 100% height.
 */
export function SafeResponsiveChart({ className, children, minSize = 24, option, uplot, ...echartProps }) {
  const containerRef = useRef(null);
  // Track the MEASURED pixel size of the wrapper, not just a ready flag. We hand
  // those explicit px dimensions to <ResponsiveContainer> below (instead of
  // width/height="100%"), so recharts never runs its own parent measurement —
  // which initialises to -1 and logs "The width(-1) and height(-1) of chart
  // should be greater than 0" on the first mount render, once per chart. The
  // ResizeObserver keeps the chart responsive by re-measuring on layout changes.
  const [size, setSize] = useState({ width: 0, height: 0 });

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return undefined;

    let rafId = 0;
    const update = () => {
      const rect = element.getBoundingClientRect();
      const width = Math.round(rect.width);
      const height = Math.round(rect.height);
      setSize((prev) =>
        prev.width === width && prev.height === height ? prev : { width, height }
      );
    };

    update();
    const observer = new ResizeObserver(() => {
      if (rafId) cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(update);
    });
    observer.observe(element);

    return () => {
      observer.disconnect();
      if (rafId) cancelAnimationFrame(rafId);
    };
  }, [minSize]);

  const isReady = size.width > minSize && size.height > minSize;

  const placeholder = (
    <div className="h-full flex items-center justify-center text-xs text-slate-400 dark:text-slate-500">
      Preparing chart…
    </div>
  );

  // ECharts path (migration target): render the theme-aware <EChart>. EChart
  // manages its own resize, but we still gate on a measured size so it never
  // initialises at 0×0.
  if (option) {
    return (
      <div ref={containerRef} className={className}>
        {isReady ? <EChart option={option} {...echartProps} /> : placeholder}
      </div>
    );
  }

  // uPlot path (dense time-series): hand the measured pixel size to <UPlotChart>,
  // which uPlot requires (it does not self-size). Re-measured by the ResizeObserver.
  if (uplot) {
    return (
      <div ref={containerRef} className={className}>
        {isReady ? <UPlotChart {...uplot} width={size.width} height={size.height} /> : placeholder}
      </div>
    );
  }

  // Legacy recharts path — unchanged.
  return (
    <div ref={containerRef} className={className}>
      {isReady ? (
        <ResponsiveContainer width={size.width} height={size.height}>
          {children}
        </ResponsiveContainer>
      ) : (
        placeholder
      )}
    </div>
  );
}

export default SafeResponsiveChart;
