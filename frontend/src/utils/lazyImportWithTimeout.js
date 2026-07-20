/**
 * Wrap a dynamic import so hung Vite/network transforms reject instead of
 * leaving React.lazy() + Suspense on "Loading module…" forever.
 *
 * Still lazy: the factory runs only when the route is entered (or prefetched).
 */
export function lazyImportWithTimeout(importer, {
  timeoutMs = 45000,
  retries = 1,
  label = "module",
} = {}) {
  return async () => {
    let lastError;
    for (let attempt = 0; attempt <= retries; attempt += 1) {
      let timer;
      try {
        const loaded = importer();
        return await Promise.race([
          Promise.resolve(loaded).finally(() => {
            if (timer != null) {
              globalThis.clearTimeout(timer);
            }
          }),
          new Promise((_, reject) => {
            timer = globalThis.setTimeout(() => {
              reject(
                new Error(
                  `Timed out loading ${label} after ${timeoutMs}ms`
                  + (attempt ? ` (retry ${attempt})` : ""),
                ),
              );
            }, timeoutMs);
            if (typeof timer?.unref === "function") {
              timer.unref();
            }
          }),
        ]);
      } catch (err) {
        lastError = err;
        if (timer != null) {
          globalThis.clearTimeout(timer);
        }
        if (attempt >= retries) break;
      }
    }
    throw lastError instanceof Error
      ? lastError
      : new Error(`Failed to load ${label}`);
  };
}
