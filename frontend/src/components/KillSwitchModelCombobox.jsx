import { useEffect, useId, useMemo, useRef, useState } from "react";
import { ChevronDown, Loader2 } from "lucide-react";

function matchesQuery(opt, query) {
  if (!query) {
    return true;
  }
  const q = query.toLowerCase();
  const hay = `${opt.value} ${opt.label} ${opt.modelId || ""}`.toLowerCase();
  return hay.includes(q);
}

/**
 * Searchable model picker for kill-switch forms (connected models + synthetic global).
 */
export function KillSwitchModelCombobox({
  id: idProp,
  label,
  options = [],
  value = "",
  onChange,
  required = false,
  disabled = false,
  loading = false,
  placeholder = "Search or select model…",
  hintId,
  hint,
  emptyMessage,
  describedBy,
}) {
  const autoId = useId();
  const inputId = idProp || `ks-combobox-${autoId}`;
  const listboxId = `${inputId}-listbox`;
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef(null);

  const selected = useMemo(
    () => options.find((o) => o.value === value),
    [options, value],
  );

  const filtered = useMemo(
    () => options.filter((o) => !o.disabled && matchesQuery(o, query)),
    [options, query],
  );

  useEffect(() => {
    if (!open) {
      setQuery(selected?.label || value || "");
    }
  }, [open, selected, value]);

  useEffect(() => {
    const onDoc = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const pick = (opt) => {
    if (opt.disabled) {
      return;
    }
    onChange(opt.value);
    setQuery(opt.label);
    setOpen(false);
  };

  return (
    <div ref={rootRef} className="relative">
      {label ? (
        <label htmlFor={inputId} className="sr-only">
          {label}
        </label>
      ) : null}
      <div className="relative">
        <input
          id={inputId}
          type="text"
          role="combobox"
          aria-expanded={open}
          aria-controls={listboxId}
          aria-autocomplete="list"
          aria-required={required}
          aria-describedby={[describedBy, hintId].filter(Boolean).join(" ") || undefined}
          disabled={disabled || loading}
          value={open ? query : (selected?.label || value || "")}
          placeholder={placeholder}
          onFocus={() => {
            setOpen(true);
            setQuery(selected?.label || value || "");
          }}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
            if (!e.target.value) {
              onChange("");
            }
          }}
          className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full min-h-[44px] px-3 py-2 pr-9 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent disabled:opacity-60"
        />
        {loading ? (
          <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-teal-500 animate-spin" />
        ) : (
          <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none" />
        )}
      </div>

      {open && !disabled && !loading && (
        <ul
          id={listboxId}
          role="listbox"
          className="absolute z-[130] mt-1 max-h-48 w-full overflow-auto rounded-lg border border-slate-200 bg-white py-1 shadow-lg dark:border-slate-600 dark:bg-slate-800"
        >
          {filtered.length === 0 ? (
            <li className="px-3 py-2 text-xs text-slate-500 dark:text-slate-400" role="option">
              {emptyMessage || "No matching models"}
            </li>
          ) : (
            filtered.map((opt) => (
              <li key={opt.value} role="presentation">
                <button
                  type="button"
                  role="option"
                  aria-selected={value === opt.value}
                  disabled={opt.disabled}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => pick(opt)}
                  className={`w-full text-left px-3 py-2 text-sm min-h-[44px] ${
                    opt.disabled
                      ? "opacity-50 cursor-not-allowed text-slate-400"
                      : "hover:bg-teal-50 dark:hover:bg-teal-900/30 text-slate-900 dark:text-slate-100"
                  } ${value === opt.value ? "bg-teal-50/80 dark:bg-teal-900/20" : ""}`}
                >
                  <span className="block font-medium">{opt.label}</span>
                  {opt.modelId && opt.modelId !== opt.value ? (
                    <span className="block text-[11px] font-mono text-slate-500 dark:text-slate-400 mt-0.5">
                      LiteLLM id: {opt.modelId}
                    </span>
                  ) : null}
                  {opt.disabledReason ? (
                    <span className="block text-[11px] text-amber-700 dark:text-amber-300 mt-0.5">
                      {opt.disabledReason}
                    </span>
                  ) : null}
                </button>
              </li>
            ))
          )}
          {options.filter((o) => o.disabled).map((opt) => (
            <li key={`disabled-${opt.value}`} role="presentation">
              <div className="px-3 py-2 text-xs text-slate-400 border-t border-slate-100 dark:border-slate-700">
                {opt.label}
                {opt.disabledReason ? ` — ${opt.disabledReason}` : " (unavailable)"}
              </div>
            </li>
          ))}
        </ul>
      )}

      {hint ? (
        <p id={hintId} className="text-[11px] text-slate-500 dark:text-slate-400 mt-1">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
