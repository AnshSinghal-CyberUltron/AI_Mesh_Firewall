import {
  getDedicatedGatewayFallbackUrl,
  isControlPlaneGatewayUrl,
  isProductionFirewallHost,
  isViteDevUiPort,
} from "./environmentUrls.js";

export function isModelNotConfiguredResponse(status, data) {
  if (Number(status) !== 404) return false;
  const nested = data?.error;
  const code =
    data?.code
    || (typeof nested === "object" && nested?.code)
    || (typeof nested === "string" ? nested : "");
  return code === "model_not_configured";
}

export function isIsolationFailureResponse(status, data) {
  const nested = data?.error;
  const code = String(
    data?.code
    || (typeof nested === "object" && nested?.code)
    || "",
  );
  return (
    code === "isolation_target_uncallable"
    || code === "kill_switch_active"
    || code === "model_isolated"
  );
}

export function rewriteChatBodyModelAuto(body) {
  if (body == null) return body;
  const raw = typeof body === "string" ? body : JSON.stringify(body);
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return body;
    const originalModel = String(parsed.model || "").trim();
    parsed.model = "auto";
    if (!parsed.routing_preferences || typeof parsed.routing_preferences !== "object") {
      parsed.routing_preferences = {};
    }
    parsed.routing_preferences.enable_routing = true;
    const prevPreferred = String(parsed.routing_preferences.preferred_model || "").trim();
    const hint = prevPreferred && prevPreferred.toLowerCase() !== "auto"
      ? prevPreferred
      : originalModel;
    if (hint && hint.toLowerCase() !== "auto") {
      parsed.routing_preferences.preferred_model = hint;
    }
    return JSON.stringify(parsed);
  } catch {
    return body;
  }
}

function trimSlash(value) {
  return String(value || "").replace(/\/+$/, "");
}

/** Same-origin first on Vite/prod UI; skip Django control URLs. */
export function chatGatewayBases(gatewayUrl) {
  const origin = typeof window !== "undefined" ? trimSlash(window.location.origin) : "";
  const dedicated = getDedicatedGatewayFallbackUrl();
  const bases = [];
  const add = (base) => {
    if (base == null) return;
    if (base !== "" && isControlPlaneGatewayUrl(base)) return;
    if (!bases.includes(base)) bases.push(base);
  };
  if (isViteDevUiPort() || isProductionFirewallHost()) add("");
  const primary = trimSlash(gatewayUrl);
  if (primary && !isControlPlaneGatewayUrl(primary)) add(primary);
  if (origin) add(origin);
  if (dedicated) add(dedicated);
  if (bases.length === 0) add("");
  return bases;
}

function parseJsonSafe(text) {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return { raw: text };
  }
}

async function readGatewayResponse(res) {
  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("text/event-stream")) {
    const { consumeSSEStream } = await import("./liveGateway.js");
    const sse = await consumeSSEStream(res);
    return {
      ok: res.ok,
      status: res.status,
      headers: res.headers,
      sse,
      data: sse.data || sse.terminalError || null,
      isStream: true,
      contentType,
    };
  }
  const text = await res.text();
  const data = parseJsonSafe(text);
  return {
    ok: res.ok,
    status: res.status,
    headers: res.headers,
    data,
    isStream: false,
    contentType,
  };
}

function isHtml404(result) {
  if (result?.status !== 404) return false;
  if (isModelNotConfiguredResponse(result.status, result.data)) return false;
  const ct = String(result.contentType || "");
  if (ct.includes("text/html")) return true;
  const raw = typeof result.data?.raw === "string" ? result.data.raw : "";
  return /page not found/i.test(raw);
}

/**
 * POST/GET a gateway path with host + model fallbacks.
 * `opts.body` must be a string when retrying model=auto (JSON).
 */
export async function fetchGatewayPath({ gatewayUrl, gatewayKey, path, opts = {}, stream = false }) {
  const bases = chatGatewayBases(gatewayUrl);
  const timeoutMs = Number(opts.timeoutMs) || 0;
  let body = opts.body;
  let last = null;

  for (let i = 0; i < bases.length; i += 1) {
    const base = bases[i];
    const attempt = async (nextBody) => {
      const headers = {
        "Content-Type": "application/json",
        ...(stream ? { Accept: "text/event-stream" } : {}),
        ...(gatewayKey ? { Authorization: `Bearer ${gatewayKey}` } : {}),
        ...(opts.headers || {}),
      };
      const { timeoutMs: _omitTimeout, headers: _omitHeaders, ...rest } = opts;
      const fetchOpts = { ...rest, headers, body: nextBody };
      if (timeoutMs > 0 && !fetchOpts.signal) {
        fetchOpts.signal = AbortSignal.timeout(timeoutMs);
      }
      const url = `${base}${path}`;
      const res = await fetch(url, fetchOpts);
      return readGatewayResponse(res);
    };

    try {
      last = await attempt(body);
    } catch (err) {
      last = {
        ok: false,
        status: 0,
        headers: null,
        data: { error: err?.message || "network", message: err?.message || "network" },
        isStream: false,
        aborted: err?.name === "AbortError",
        timedOut: err?.name === "TimeoutError" || /timeout/i.test(String(err?.message || "")),
      };
      continue;
    }

    if (last.ok) return last;

    if (
      isModelNotConfiguredResponse(last.status, last.data)
      && body
      && !isIsolationFailureResponse(last.status, last.data)
    ) {
      const rewritten = rewriteChatBodyModelAuto(body);
      if (rewritten !== body) {
        body = rewritten;
        try {
          const retried = await attempt(body);
          if (retried.ok) return retried;
          last = retried;
        } catch {
          /* keep last */
        }
      }
    }

    if (isHtml404(last)) {
      continue;
    }
    return last;
  }

  return last || {
    ok: false,
    status: 0,
    headers: null,
    data: { error: "unreachable", message: "Gateway did not respond." },
    isStream: false,
  };
}
