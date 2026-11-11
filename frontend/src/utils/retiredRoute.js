// GW35: with the control plane on gateway v2, the proxies for routes v2 does not implement answer
// HTTP 410 `retired_on_gateway_v2`. These helpers turn that answer into a labelled panel model, so
// a page shows "retired" and never a spinner, an empty chart or an invented success.
export const RETIRED_CODE = "retired_on_gateway_v2";

// True for the 410 envelope `{status:"error", data:{code:"retired_on_gateway_v2", ...}}`.
export function isRetiredResponse(status, body) {
  if (status !== 410 || !body || typeof body !== "object") return false;
  const code = body.data?.code ?? body.code;
  return code === RETIRED_CODE;
}

// What the panel shows: a title, the reason the server gave, and the retired path.
export function retiredPanelModel(body) {
  const data = (body && body.data) || body || {};
  return {
    title: "Retired on gateway v2",
    message: String(data.message || "This feature is not part of gateway v2."),
    path: String(data.path || ""),
  };
}

// Read a fetch Response and return a panel model when it is the retired answer, else null.
// Clones first, so the caller can still read the body.
export async function retiredFromResponse(res) {
  if (!res || res.status !== 410) return null;
  let body = null;
  try {
    body = await (typeof res.clone === "function" ? res.clone() : res).json();
  } catch {
    return null;
  }
  return isRetiredResponse(res.status, body) ? retiredPanelModel(body) : null;
}

// The sync endpoint reports a retired discovery as an error string that begins with the code.
export function retiredFromMessage(text) {
  if (typeof text !== "string" || !text.startsWith(`${RETIRED_CODE}:`)) return null;
  return {
    title: "Retired on gateway v2",
    message: text.slice(RETIRED_CODE.length + 1).trim(),
    path: "",
  };
}
