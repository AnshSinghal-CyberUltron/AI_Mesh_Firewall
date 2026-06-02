import { createContext, useContext, useState, useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import { CheckCircle, AlertTriangle, Info, X } from "lucide-react";
import { cn } from "../../lib/utils";

const ToastCtx = createContext(null);

/** No-op safe outside a provider so components never crash. */
export function useToast() {
  return useContext(ToastCtx) || { toast: () => {} };
}

const ICONS = { success: CheckCircle, error: AlertTriangle, info: Info };
const TONES = {
  success: "border-teal-200 dark:border-teal-800",
  error: "border-red-200 dark:border-red-800",
  info: "border-blue-200 dark:border-blue-800",
};
const ICON_TONES = {
  success: "text-teal-600 dark:text-teal-400",
  error: "text-red-600 dark:text-red-400",
  info: "text-blue-600 dark:text-blue-400",
};

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const idRef = useRef(0);
  const remove = useCallback((id) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const toast = useCallback(
    (msg, opts = {}) => {
      const id = ++idRef.current;
      setToasts((t) => [...t, { id, msg, tone: opts.tone || "info" }]);
      setTimeout(() => remove(id), opts.duration || 3800);
      return id;
    },
    [remove]
  );

  return (
    <ToastCtx.Provider value={{ toast }}>
      {children}
      {createPortal(
        <div
          className="fixed top-4 right-4 z-[60] flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2"
          role="region"
          aria-label="Notifications"
        >
          {toasts.map((t) => {
            const Icon = ICONS[t.tone] || Info;
            return (
              <div
                key={t.id}
                role="status"
                className={cn(
                  "flex items-start gap-2.5 rounded-xl border bg-white dark:bg-slate-900 px-3.5 py-3 text-sm shadow-lg",
                  TONES[t.tone]
                )}
              >
                <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", ICON_TONES[t.tone])} aria-hidden="true" />
                <span className="flex-1 text-slate-700 dark:text-slate-200">{t.msg}</span>
                <button
                  type="button"
                  aria-label="Dismiss notification"
                  onClick={() => remove(t.id)}
                  className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            );
          })}
        </div>,
        document.body
      )}
    </ToastCtx.Provider>
  );
}
