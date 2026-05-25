import { Info } from "lucide-react";
import { useState, useRef, useEffect } from "react";
import { createPortal } from "react-dom";

export function InfoTooltip({ title, text, children }) {
  const [show, setShow] = useState(false);
  const [position, setPosition] = useState({ top: 0, left: 0, width: 320 });
  const [lockedOpen, setLockedOpen] = useState(false);
  const wrapperRef = useRef(null);
  const buttonRef = useRef(null);
  const tooltipRef = useRef(null);
  const hideTimerRef = useRef(null);
  const lockedOpenRef = useRef(false);

  const clearHideTimer = () => {
    if (hideTimerRef.current) {
      clearTimeout(hideTimerRef.current);
      hideTimerRef.current = null;
    }
  };

  const openTooltip = () => {
    clearHideTimer();
    setShow(true);
  };

  const scheduleClose = () => {
    clearHideTimer();
    hideTimerRef.current = setTimeout(() => {
      if (!lockedOpenRef.current) setShow(false);
    }, 120);
  };

  useEffect(() => {
    if (!show || !buttonRef.current) return;

    const updatePosition = () => {
      const rect = buttonRef.current.getBoundingClientRect();
      const viewportPadding = 8;
      const tooltipWidth = Math.min(360, Math.max(280, window.innerWidth - viewportPadding * 2));
      const centeredLeft = rect.left + rect.width / 2 - tooltipWidth / 2;
      const clampedLeft = Math.max(viewportPadding, Math.min(centeredLeft, window.innerWidth - tooltipWidth - viewportPadding));

      setPosition({
        top: rect.bottom + 10,
        left: clampedLeft,
        width: tooltipWidth,
      });
    };

    updatePosition();
    window.addEventListener("scroll", updatePosition, true);
    window.addEventListener("resize", updatePosition);
    return () => {
      window.removeEventListener("scroll", updatePosition, true);
      window.removeEventListener("resize", updatePosition);
    };
  }, [show]);

  useEffect(() => {
    lockedOpenRef.current = lockedOpen;
  }, [lockedOpen]);

  useEffect(() => {
    return () => {
      clearHideTimer();
    };
  }, []);

  useEffect(() => {
    if (!show) return;
    const handleClickOutside = (e) => {
      const clickedTrigger = wrapperRef.current && wrapperRef.current.contains(e.target);
      const clickedTooltip = tooltipRef.current && tooltipRef.current.contains(e.target);
      if (!clickedTrigger && !clickedTooltip) {
        setLockedOpen(false);
        setShow(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [show]);

  return (
    <div className="inline-block ml-2" ref={wrapperRef}>
      <button
        ref={buttonRef}
        onClick={() => {
          clearHideTimer();
          const shouldOpen = !(show && lockedOpenRef.current);
          if (shouldOpen) {
            setLockedOpen(true);
            setShow(true);
          } else {
            setLockedOpen(false);
            setShow(false);
          }
        }}
        onMouseEnter={openTooltip}
        onMouseLeave={scheduleClose}
        onFocus={openTooltip}
        onBlur={scheduleClose}
        className="p-1 text-slate-400 hover:text-teal-400 transition-colors"
        aria-label="Info"
        aria-expanded={show}
        type="button"
      >
        <Info className="w-4 h-4" />
      </button>
      {show && createPortal(
        <div
          ref={tooltipRef}
          className="fixed z-[120] rounded-lg border border-slate-200 bg-white p-4 text-sm shadow-xl dark:border-slate-700 dark:bg-slate-800"
          style={{ top: position.top, left: position.left, width: `${position.width}px` }}
          role="dialog"
          aria-label={title || "Info tooltip"}
          onMouseEnter={openTooltip}
          onMouseLeave={scheduleClose}
        >
          {title && <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-teal-600 dark:text-teal-400">{title}</h4>}
          <div className="text-xs leading-relaxed whitespace-pre-line text-slate-600 dark:text-slate-300">{children || text}</div>
          <button
            onClick={() => {
              setLockedOpen(false);
              setShow(false);
            }}
            className="mt-3 text-xs text-slate-400 hover:text-slate-600 dark:hover:text-slate-300"
          >
            Close
          </button>
        </div>,
        document.body,
      )}
    </div>
  );
}
