/** Phase 0a F-b: bounded fetch abort. Default 30s, well below nginx 300s. */

export const DEFAULT_FETCH_TIMEOUT_MS = 30_000;

export function isDocumentHidden() {
  return typeof document !== "undefined" && document.hidden === true;
}

export function composeAbortSignal(userSignal, timeoutMs = DEFAULT_FETCH_TIMEOUT_MS) {
  const timeoutSignal = AbortSignal.timeout(timeoutMs);
  if (!userSignal) {
    return timeoutSignal;
  }
  if (typeof AbortSignal.any === "function") {
    return AbortSignal.any([userSignal, timeoutSignal]);
  }
  const merged = new AbortController();
  const abortMerged = () => {
    if (!merged.signal.aborted) merged.abort();
  };
  if (userSignal.aborted || timeoutSignal.aborted) {
    abortMerged();
    return merged.signal;
  }
  userSignal.addEventListener("abort", abortMerged, { once: true });
  timeoutSignal.addEventListener("abort", abortMerged, { once: true });
  return merged.signal;
}
