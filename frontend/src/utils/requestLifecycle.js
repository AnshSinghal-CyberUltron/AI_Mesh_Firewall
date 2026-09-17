/** Phase 0a F-b: bounded fetch abort. Default 30s, well below nginx 300s. */

export const DEFAULT_FETCH_TIMEOUT_MS = 30_000;
export const HEAVY_ANALYTICS_CONCURRENCY = 2;
export const ANALYTICS_DEDUPE_TTL_MS = 2500;

/** True when control rejected a heavy analytics GET with the per-worker semaphore. */
export function isAnalyticsBusyPayload(data) {
  if (!data || typeof data !== "object") return false;
  return data.error === "analytics_busy";
}

export function isLiveGeneration(signal, gen, currentGen) {
  return !signal?.aborted && gen === currentGen;
}

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

export async function mapWithConcurrency(items, limit, mapper, signal) {
  const list = Array.from(items || []);
  const n = list.length;
  const out = new Array(n);
  let next = 0;
  const workers = Math.min(Math.max(1, Number(limit) || 1), Math.max(n, 1));

  const abortError = () => {
    const err = new Error("aborted");
    err.name = "AbortError";
    return err;
  };

  async function worker() {
    while (true) {
      if (signal?.aborted) throw abortError();
      const i = next;
      next += 1;
      if (i >= n) return;
      out[i] = await mapper(list[i], i, signal);
    }
  }

  if (n === 0) return out;
  await Promise.all(Array.from({ length: workers }, () => worker()));
  return out;
}

