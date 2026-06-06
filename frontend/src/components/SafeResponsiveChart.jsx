import { useEffect, useRef, useState } from "react";
import { ResponsiveContainer } from "recharts";

/**
 * ResizeObserver-gated wrapper around recharts' <ResponsiveContainer>.
 *
 * recharts measures its parent synchronously on mount; when a chart is mounted
 * inside a just-expanded collapsible section (or any container that is 0×0 for
 * a frame), it logs "The width(-1) and height(-1) of chart should be greater
 * than 0". This wrapper only renders the ResponsiveContainer once the wrapping
 * div has a real measured size, eliminating that warning.
 *
 * The wrapper div must carry an explicit height (via `className`, e.g. h-[200px]),
 * since ResponsiveContainer uses height="100%".
 */
export function SafeResponsiveChart({ className, children, minSize = 24 }) {
  const containerRef = useRef(null);
  const [isReady, setIsReady] = useState(false);

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return undefined;

    let rafId = 0;
    const update = () => {
      const rect = element.getBoundingClientRect();
      setIsReady(rect.width > minSize && rect.height > minSize);
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

  return (
    <div ref={containerRef} className={className}>
      {isReady ? (
        <ResponsiveContainer width="100%" height="100%">
          {children}
        </ResponsiveContainer>
      ) : (
        <div className="h-full flex items-center justify-center text-xs text-slate-400 dark:text-slate-500">
          Preparing chart…
        </div>
      )}
    </div>
  );
}

export default SafeResponsiveChart;
