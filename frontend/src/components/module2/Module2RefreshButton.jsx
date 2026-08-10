import { useEffect, useRef, useState } from "react";
import { createPortal, flushSync } from "react-dom";
import { RefreshCw } from "lucide-react";

const MIN_BUSY_MS = 1200;

/**
 * Module 2 refresh control.
 * Busy feedback is driven by React state (label, colors, toast, JS pulse) so it
 * remains visible even when prefers-reduced-motion disables CSS animations.
 */
export function Module2RefreshButton({
  onRefresh,
  label = "Refresh",
  className = "",
  disabled = false,
}) {
  const [busy, setBusy] = useState(false);
  const [pulse, setPulse] = useState(false);
  const [degrees, setDegrees] = useState(0);
  const inFlightRef = useRef(false);

  useEffect(() => {
    if (!busy) {
      setPulse(false);
      setDegrees(0);
      return undefined;
    }
    setPulse(true);
    const pulseId = window.setInterval(() => setPulse((v) => !v), 350);
    const spinId = window.setInterval(() => setDegrees((d) => (d + 30) % 360), 50);
    return () => {
      window.clearInterval(pulseId);
      window.clearInterval(spinId);
    };
  }, [busy]);

  const handleClick = async (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (inFlightRef.current || disabled || typeof onRefresh !== "function") return;

    inFlightRef.current = true;
    flushSync(() => setBusy(true));

    const started = Date.now();
    try {
      await Promise.resolve(onRefresh());
    } catch {
      // Callers surface errors; never leave the control stuck.
    } finally {
      const remaining = MIN_BUSY_MS - (Date.now() - started);
      if (remaining > 0) {
        await new Promise((resolve) => setTimeout(resolve, remaining));
      }
      flushSync(() => setBusy(false));
      inFlightRef.current = false;
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={handleClick}
        disabled={disabled || busy}
        aria-label={busy ? "Updating" : label}
        aria-busy={busy}
        title={busy ? "Updating…" : label}
        data-testid="module2-refresh-button"
        data-busy={busy ? "true" : "false"}
        style={busy ? { backgroundColor: pulse ? "#0d9488" : "#0f766e" } : undefined}
        className={`inline-flex min-h-9 min-w-[8rem] items-center justify-center gap-2 rounded-lg border px-3 py-2 text-sm font-semibold shadow-sm disabled:cursor-wait ${
          busy
            ? "border-teal-400 text-white"
            : "border-slate-200 bg-white text-slate-700 hover:border-teal-400 hover:bg-teal-50 hover:text-teal-800 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200 dark:hover:border-teal-600 dark:hover:bg-teal-950/50"
        } ${className}`}
      >
        <RefreshCw
          className="h-4 w-4 shrink-0"
          aria-hidden
          style={busy ? { transform: `rotate(${degrees}deg)` } : undefined}
        />
        <span>{busy ? "Updating…" : label}</span>
      </button>

      {busy &&
        typeof document !== "undefined" &&
        createPortal(
          <div
            role="status"
            aria-live="assertive"
            data-testid="module2-refresh-toast"
            style={{ backgroundColor: pulse ? "#0d9488" : "#0f766e" }}
            className="pointer-events-none fixed left-1/2 top-4 z-[9999] -translate-x-1/2 rounded-full border-2 border-white px-5 py-2.5 text-sm font-bold text-white shadow-2xl"
          >
            Updating page data…
          </div>,
          document.body,
        )}
    </>
  );
}
