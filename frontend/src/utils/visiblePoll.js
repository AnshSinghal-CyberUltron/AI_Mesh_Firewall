import { isDocumentHidden } from "./requestLifecycle.js";

/** Phase 0a F-a: skip interval ticks while the document is hidden. */
export function startVisibleInterval(fn, ms) {
  const id = setInterval(() => {
    if (!isDocumentHidden()) fn();
  }, ms);
  return () => clearInterval(id);
}
