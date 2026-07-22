import { useEffect, useRef, useCallback } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { cn } from "../../lib/utils";

/**
 * Accessible modal dialog: portal + focus trap + ESC-to-close + aria-modal +
 * scroll lock + focus restore. Custom (no extra deps), matching house tokens.
 */
export function Dialog({ open, onClose, labelledBy, children, className }) {
  const ref = useRef(null);
  const prevFocus = useRef(null);

  // FOCUS-LOSS ROOT FIX: callers pass an inline `onClose` (new identity every
  // render). If `handleKey` (and therefore the mount/focus-trap effect below)
  // depended on `onClose`, the effect would tear down + re-run on EVERY parent
  // re-render — e.g. once per keystroke in a controlled form inside the modal.
  // Its cleanup calls `prevFocus.focus()` and its setTimeout re-autofocuses the
  // first focusable element, stealing focus away from the field being typed in.
  // Keep the latest onClose in a ref so `handleKey` stays stable and the effect
  // runs only when `open` actually toggles.
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  const handleKey = useCallback((e) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      onCloseRef.current?.();
      return;
    }
    if (e.key === "Tab" && ref.current) {
      const focusable = ref.current.querySelectorAll(
        'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])'
      );
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    prevFocus.current = document.activeElement;
    const t = setTimeout(() => {
      const el = ref.current?.querySelector(
        '[data-autofocus],input,textarea,select,button'
      );
      el?.focus();
    }, 0);
    document.addEventListener("keydown", handleKey, true);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      clearTimeout(t);
      document.removeEventListener("keydown", handleKey, true);
      document.body.style.overflow = prevOverflow;
      if (prevFocus.current instanceof HTMLElement) prevFocus.current.focus();
    };
  }, [open, handleKey]);

  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="presentation">
      <div
        className="absolute inset-0 bg-slate-900/50 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        className={cn(
          "relative w-full max-w-2xl max-h-[90vh] overflow-y-auto rounded-2xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 shadow-2xl",
          className
        )}
      >
        {children}
      </div>
    </div>,
    document.body
  );
}

export function DialogHeader({ title, description, onClose, id }) {
  return (
    <div className="flex items-start justify-between gap-4 p-5 border-b border-slate-200 dark:border-slate-700 sticky top-0 bg-white/95 dark:bg-slate-900/95 backdrop-blur z-10">
      <div>
        <h2 id={id} className="text-base font-semibold text-slate-900 dark:text-slate-100">
          {title}
        </h2>
        {description ? (
          <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">{description}</p>
        ) : null}
      </div>
      {onClose ? (
        <button
          type="button"
          onClick={onClose}
          aria-label="Close dialog"
          className="shrink-0 rounded-lg p-1.5 text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
        >
          <X className="w-5 h-5" />
        </button>
      ) : null}
    </div>
  );
}

export function DialogBody({ children, className }) {
  return <div className={cn("p-5 space-y-4", className)}>{children}</div>;
}

export function DialogFooter({ children, className }) {
  return (
    <div
      className={cn(
        "flex items-center justify-end gap-2 p-5 border-t border-slate-200 dark:border-slate-700 sticky bottom-0 bg-white/95 dark:bg-slate-900/95 backdrop-blur",
        className
      )}
    >
      {children}
    </div>
  );
}
