import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  FlaskConical,
  Loader2,
  Play,
  RefreshCw,
  Server,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Wrench,
} from "lucide-react";
import { useAuth } from "../../context/AuthContext";

/**
 * MCPGuardrailSimulator — live MCP tool-call sandbox for the active org.
 *
 * Lists MCP servers + tools registered for the current organization,
 * lets the operator pick one, edit JSON arguments, then either:
 *   • Dry-Run  → POST /api/policies/test/   (no enforcement event, no tool exec)
 *   • Live     → POST /api/mcp-connector/tools/call/ (real call, real audit)
 *
 * Renders the unified policy decision (action, matched policies/rules)
 * and the raw tool result side-by-side. Enforcement is driven entirely
 * by the org's MCP Security Policies — there is no separate guardrail
 * profile layer.
 */

const MODE_DRYRUN = "dryrun";
const MODE_LIVE = "live";

function classNames(...xs) {
  return xs.filter(Boolean).join(" ");
}

function pretty(value, fallback = "—") {
  if (value == null) return fallback;
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function flattenArgs(args) {
  if (args == null) return "";
  try {
    return typeof args === "string" ? args : JSON.stringify(args);
  } catch {
    return String(args);
  }
}

function actionTone(action) {
  const a = String(action || "").toLowerCase();
  if (a === "block" || a === "deny") {
    return {
      label: "BLOCK",
      icon: ShieldAlert,
      ring: "border-red-300 bg-red-50/80 dark:border-red-700 dark:bg-red-900/20",
      text: "text-red-700 dark:text-red-300",
    };
  }
  if (a === "redact") {
    return {
      label: "REDACT",
      icon: Shield,
      ring: "border-amber-300 bg-amber-50/80 dark:border-amber-700 dark:bg-amber-900/20",
      text: "text-amber-700 dark:text-amber-300",
    };
  }
  if (a === "monitor") {
    return {
      label: "MONITOR",
      icon: Activity,
      ring: "border-sky-300 bg-sky-50/80 dark:border-sky-700 dark:bg-sky-900/20",
      text: "text-sky-700 dark:text-sky-300",
    };
  }
  return {
    label: "ALLOW",
    icon: ShieldCheck,
    ring: "border-emerald-300 bg-emerald-50/80 dark:border-emerald-700 dark:bg-emerald-900/20",
    text: "text-emerald-700 dark:text-emerald-300",
  };
}

function defaultArgsForSchema(schema) {
  if (!schema || typeof schema !== "object") return {};
  const props = schema.properties || {};
  const required = Array.isArray(schema.required) ? schema.required : [];
  const out = {};
  for (const [name, def] of Object.entries(props)) {
    if (!def || typeof def !== "object") continue;
    if (Object.prototype.hasOwnProperty.call(def, "default")) {
      out[name] = def.default;
      continue;
    }
    if (!required.includes(name)) continue;
    switch (def.type) {
      case "string":
        out[name] = "";
        break;
      case "number":
      case "integer":
        out[name] = 0;
        break;
      case "boolean":
        out[name] = false;
        break;
      case "array":
        out[name] = [];
        break;
      case "object":
        out[name] = {};
        break;
      default:
        out[name] = null;
    }
  }
  return out;
}

export function MCPGuardrailSimulator() {
  const { fetchWithAuth } = useAuth();

  const [servers, setServers] = useState([]);
  const [serverId, setServerId] = useState("");
  const [serverTools, setServerTools] = useState([]);
  const [toolsLoading, setToolsLoading] = useState(false);
  const [toolName, setToolName] = useState("");

  const [argsText, setArgsText] = useState("{}");
  const [argsError, setArgsError] = useState(null);

  const [mode, setMode] = useState(MODE_DRYRUN);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const [loadError, setLoadError] = useState(null);

  // Initial load: org MCP servers
  const loadAll = useCallback(async () => {
    setLoadError(null);
    try {
      const srvRes = await fetchWithAuth("/api/mcp-connector/servers/");
      if (!srvRes.ok) throw new Error(`servers HTTP ${srvRes.status}`);
      const srvData = await srvRes.json();
      const list = Array.isArray(srvData) ? srvData : srvData.results || [];
      setServers(list);
      setServerId((prev) => prev || (list.length ? list[0].id : ""));
    } catch (e) {
      setLoadError(e.message || "Failed to load org context.");
    }
  }, [fetchWithAuth]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  // When server changes → load its tools
  useEffect(() => {
    if (!serverId) {
      setServerTools([]);
      setToolName("");
      return;
    }
    let cancelled = false;
    (async () => {
      setToolsLoading(true);
      try {
        const res = await fetchWithAuth(`/api/mcp-connector/servers/${serverId}/tools/`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const list = Array.isArray(data) ? data : data.results || [];
        if (cancelled) return;
        setServerTools(list);
        const first = list.find((t) => t?.tool_name) || list[0];
        const firstName = first?.tool_name || first?.name || "";
        setToolName(firstName);
      } catch (e) {
        if (!cancelled) {
          setServerTools([]);
          setToolName("");
        }
      } finally {
        if (!cancelled) setToolsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [serverId, fetchWithAuth]);

  // When tool changes → seed JSON args from input_schema defaults
  useEffect(() => {
    if (!toolName) {
      setArgsText("{}");
      setArgsError(null);
      return;
    }
    const tool = serverTools.find(
      (t) => (t?.tool_name || t?.name) === toolName,
    );
    const seed = defaultArgsForSchema(tool?.input_schema);
    setArgsText(JSON.stringify(seed, null, 2));
    setArgsError(null);
  }, [toolName, serverTools]);

  const selectedServer = useMemo(
    () => servers.find((s) => String(s.id) === String(serverId)) || null,
    [servers, serverId],
  );
  const selectedTool = useMemo(
    () =>
      serverTools.find((t) => (t?.tool_name || t?.name) === toolName) || null,
    [serverTools, toolName],
  );

  const parsedArgs = useMemo(() => {
    try {
      const parsed = JSON.parse(argsText || "{}");
      return { ok: true, value: parsed };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  }, [argsText]);

  const onSubmit = useCallback(async () => {
    setError(null);
    setResult(null);
    if (!toolName || !selectedServer) {
      setError("Pick a server and tool first.");
      return;
    }
    if (!parsedArgs.ok) {
      setArgsError(parsedArgs.error);
      return;
    }
    setArgsError(null);
    setSubmitting(true);
    try {
      let res;
      if (mode === MODE_DRYRUN) {
        res = await fetchWithAuth("/api/policies/test/", {
          method: "POST",
          body: JSON.stringify({
            policy_domain: "mcp",
            // Structured args drive per-key (scope=key) matching server-side.
            input_args: parsedArgs.value,
            // Flattened prompt kept for back-compat with entire-scope text
            // rules and older evaluators.
            prompt: `tool:${toolName} ${flattenArgs(parsedArgs.value)}`,
            response: "",
            metadata: {
              tool_name: toolName,
              server_slug: selectedServer.server_slug,
            },
          }),
        });
      } else {
        res = await fetchWithAuth("/api/mcp-connector/tools/call/", {
          method: "POST",
          body: JSON.stringify({
            name: toolName,
            server_slug: selectedServer.server_slug,
            arguments: parsedArgs.value,
          }),
        });
      }
      const status = res.status;
      let data = null;
      try {
        data = await res.json();
      } catch {
        data = null;
      }
      setResult({ status, mode, data });
    } catch (e) {
      setError(e.message || "Request failed.");
    } finally {
      setSubmitting(false);
    }
  }, [fetchWithAuth, mode, parsedArgs, selectedServer, toolName]);

  const verdict = useMemo(() => {
    if (!result) return null;
    const { status, data, mode: m } = result;
    if (m === MODE_DRYRUN) {
      const action = data?.action || (status === 200 ? "allow" : "error");
      return {
        action,
        matched_policies: data?.matched_policies || [],
        matched_rules: data?.matched_rules || [],
        message: data?.message || data?.detail || "",
        blocked: action === "block",
        redacted_input_args: data?.redacted_input_args,
        redacted_output: data?.redacted_output,
      };
    }
    if (status === 403) {
      return {
        action: "block",
        matched_policies: data?.matched_policies || [],
        matched_rules: data?.matched_rules || [],
        message: data?.error || data?.reason || "Blocked by policy",
        blocked: true,
      };
    }
    if (status >= 200 && status < 300) {
      return {
        action: data?.decision || "allow",
        matched_policies: [],
        matched_rules: [],
        message: "Tool executed by gateway.",
        blocked: false,
      };
    }
    return {
      action: "error",
      matched_policies: [],
      matched_rules: [],
      message: data?.detail || data?.error || `HTTP ${status}`,
      blocked: false,
    };
  }, [result]);

  const verdictTone = verdict ? actionTone(verdict.action) : null;
  const VerdictIcon = verdictTone?.icon;

  return (
    <div className="rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-950">
      {/* Header */}
      <div className="flex flex-col gap-2 border-b border-slate-200 px-5 py-4 dark:border-slate-800 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <FlaskConical className="h-5 w-5 text-indigo-600 dark:text-indigo-300" />
            <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100">
              MCP Policy Simulator
            </h3>
          </div>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            Pick a registered MCP server and tool, edit arguments, then run a
            dry-run policy evaluation or a real gateway call. The decision,
            matched policies, and matched rules are shown live.
          </p>
        </div>
        <button
          type="button"
          onClick={loadAll}
          className="inline-flex items-center gap-1.5 self-start rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          Refresh
        </button>
      </div>

      {loadError && (
        <div className="mx-5 mt-4 rounded-xl border border-red-300 bg-red-50/80 p-3 text-xs text-red-700 dark:border-red-700 dark:bg-red-900/20 dark:text-red-300">
          {loadError}
        </div>
      )}

      <div className="grid grid-cols-1 gap-5 p-5 lg:grid-cols-2">
        {/* LEFT: Inputs */}
        <div className="space-y-4">
          <div>
            <label className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-slate-700 dark:text-slate-300">
              <Server className="h-3.5 w-3.5" />
              MCP Server
            </label>
            <select
              value={serverId}
              onChange={(e) => setServerId(e.target.value)}
              className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 focus:border-indigo-500 focus:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
            >
              {servers.length === 0 && <option value="">No servers registered</option>}
              {servers.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} {s.server_slug ? `(${s.server_slug})` : ""} ·{" "}
                  {s.transport || "?"} · {s.is_active ? "active" : "disabled"}
                </option>
              ))}
            </select>
            {selectedServer && (
              <p className="mt-1 truncate text-[11px] text-slate-500 dark:text-slate-400">
                {selectedServer.url || selectedServer.gateway_endpoint || "—"}
              </p>
            )}
          </div>

          <div>
            <label className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-slate-700 dark:text-slate-300">
              <Wrench className="h-3.5 w-3.5" />
              Tool
              {toolsLoading && <Loader2 className="ml-1 h-3 w-3 animate-spin" />}
            </label>
            <select
              value={toolName}
              onChange={(e) => setToolName(e.target.value)}
              disabled={!serverTools.length}
              className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 focus:border-indigo-500 focus:outline-none disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
            >
              {!serverTools.length && (
                <option value="">No tools discovered for this server</option>
              )}
              {serverTools.map((t) => {
                const n = t.tool_name || t.name;
                return (
                  <option key={n} value={n}>
                    {n}
                    {t.enabled === false ? "  (disabled)" : ""}
                  </option>
                );
              })}
            </select>
            {selectedTool?.description && (
              <p className="mt-1 line-clamp-2 text-[11px] text-slate-500 dark:text-slate-400">
                {selectedTool.description}
              </p>
            )}
          </div>

          <div>
            <label className="mb-1 flex items-center justify-between text-xs font-semibold text-slate-700 dark:text-slate-300">
              <span>Arguments (JSON)</span>
              {selectedTool?.input_schema && (
                <button
                  type="button"
                  onClick={() =>
                    setArgsText(
                      JSON.stringify(
                        defaultArgsForSchema(selectedTool.input_schema),
                        null,
                        2,
                      ),
                    )
                  }
                  className="text-[11px] font-normal text-indigo-600 hover:underline dark:text-indigo-300"
                >
                  Reset to schema defaults
                </button>
              )}
            </label>
            <textarea
              spellCheck={false}
              value={argsText}
              onChange={(e) => setArgsText(e.target.value)}
              rows={8}
              className={classNames(
                "w-full rounded-lg border bg-slate-50 p-3 font-mono text-[12px] text-slate-800 focus:outline-none dark:bg-slate-900 dark:text-slate-100",
                argsError || !parsedArgs.ok
                  ? "border-red-400 focus:border-red-500"
                  : "border-slate-200 focus:border-indigo-500 dark:border-slate-700",
              )}
            />
            {(argsError || !parsedArgs.ok) && (
              <p className="mt-1 text-[11px] text-red-600 dark:text-red-300">
                {argsError || parsedArgs.error}
              </p>
            )}
          </div>

          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div className="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-0.5 dark:border-slate-700 dark:bg-slate-900">
              <button
                type="button"
                onClick={() => setMode(MODE_DRYRUN)}
                className={classNames(
                  "rounded-md px-3 py-1.5 text-xs font-medium transition",
                  mode === MODE_DRYRUN
                    ? "bg-white text-indigo-700 shadow-sm dark:bg-slate-950 dark:text-indigo-300"
                    : "text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200",
                )}
              >
                Dry-Run
              </button>
              <button
                type="button"
                onClick={() => setMode(MODE_LIVE)}
                className={classNames(
                  "rounded-md px-3 py-1.5 text-xs font-medium transition",
                  mode === MODE_LIVE
                    ? "bg-white text-rose-700 shadow-sm dark:bg-slate-950 dark:text-rose-300"
                    : "text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200",
                )}
              >
                Live Call
              </button>
            </div>
            <button
              type="button"
              onClick={onSubmit}
              disabled={submitting || !toolName || !selectedServer}
              className={classNames(
                "inline-flex items-center justify-center gap-1.5 rounded-lg px-4 py-2 text-sm font-semibold text-white transition",
                mode === MODE_DRYRUN
                  ? "bg-indigo-600 hover:bg-indigo-700"
                  : "bg-rose-600 hover:bg-rose-700",
                "disabled:cursor-not-allowed disabled:opacity-50",
              )}
            >
              {submitting ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Play className="h-4 w-4" />
              )}
              {mode === MODE_DRYRUN ? "Evaluate Policies" : "Invoke Tool"}
            </button>
          </div>
        </div>

        {/* RIGHT: Result */}
        <div className="space-y-3">
          {error && (
            <div className="rounded-xl border border-red-300 bg-red-50/80 p-3 text-xs text-red-700 dark:border-red-700 dark:bg-red-900/20 dark:text-red-300">
              <div className="flex items-center gap-1.5 font-semibold">
                <AlertTriangle className="h-3.5 w-3.5" />
                Request Error
              </div>
              <div className="mt-1">{error}</div>
            </div>
          )}

          {!result && !error && (
            <div className="flex h-full min-h-[260px] flex-col items-center justify-center rounded-xl border border-dashed border-slate-300 bg-slate-50/60 px-6 py-10 text-center text-xs text-slate-500 dark:border-slate-700 dark:bg-slate-900/60 dark:text-slate-400">
              <FlaskConical className="mb-2 h-6 w-6 text-slate-400 dark:text-slate-500" />
              Pick a server, choose a tool, edit arguments, then run
              <strong className="mx-1 text-slate-700 dark:text-slate-200">Dry-Run</strong>
              or
              <strong className="mx-1 text-slate-700 dark:text-slate-200">Live Call</strong>.
            </div>
          )}

          {result && verdict && verdictTone && (
            <>
              <div className={classNames("rounded-xl border p-4", verdictTone.ring)}>
                <div className="flex items-center gap-2">
                  {VerdictIcon && <VerdictIcon className={classNames("h-4 w-4", verdictTone.text)} />}
                  <span className={classNames("text-sm font-semibold", verdictTone.text)}>
                    {verdictTone.label}
                  </span>
                  <span className="ml-auto rounded-full bg-white/70 px-2 py-0.5 text-[10px] font-medium text-slate-600 dark:bg-slate-950/60 dark:text-slate-300">
                    {result.mode === MODE_DRYRUN ? "DRY-RUN" : "LIVE"} · HTTP {result.status}
                  </span>
                </div>
                {verdict.message && (
                  <p className="mt-1 text-xs text-slate-600 dark:text-slate-300">
                    {verdict.message}
                  </p>
                )}
              </div>

              {(verdict.matched_policies?.length || verdict.matched_rules?.length) ? (
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  <div className="rounded-xl border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-950">
                    <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                      Matched Policies ({verdict.matched_policies.length})
                    </div>
                    <ul className="space-y-1 text-xs">
                      {verdict.matched_policies.map((p) => (
                        <li key={p} className="flex items-center gap-1 font-mono text-slate-700 dark:text-slate-200">
                          <ChevronRight className="h-3 w-3 text-slate-400" />
                          {p}
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div className="rounded-xl border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-950">
                    <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                      Matched Rules ({verdict.matched_rules.length})
                    </div>
                    <ul className="space-y-1 text-xs">
                      {verdict.matched_rules.map((r) => (
                        <li key={r} className="flex items-center gap-1 text-slate-700 dark:text-slate-200">
                          <ChevronRight className="h-3 w-3 text-slate-400" />
                          {r}
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              ) : (
                <div className="rounded-xl border border-emerald-200 bg-emerald-50/60 p-3 text-xs text-emerald-700 dark:border-emerald-700 dark:bg-emerald-900/20 dark:text-emerald-300">
                  <div className="flex items-center gap-1.5 font-semibold">
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    No policies matched
                  </div>
                  <p className="mt-0.5">
                    The arguments did not trigger any active policy/rule for the
                    {" "}<code className="font-mono">mcp</code> domain in this org.
                  </p>
                </div>
              )}

              {result.mode === MODE_DRYRUN &&
                (verdict.redacted_input_args !== undefined ||
                  (verdict.redacted_output !== undefined && verdict.redacted_output !== "")) && (
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  {verdict.redacted_input_args !== undefined && (
                    <div className="rounded-xl border border-amber-200 bg-amber-50/60 p-3 dark:border-amber-700/60 dark:bg-amber-900/20">
                      <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-300">
                        Redacted Input Preview
                      </div>
                      <pre className="max-h-48 overflow-auto text-[11px] text-amber-900 dark:text-amber-100">
{pretty(verdict.redacted_input_args)}
                      </pre>
                    </div>
                  )}
                  {verdict.redacted_output !== undefined && verdict.redacted_output !== "" && (
                    <div className="rounded-xl border border-amber-200 bg-amber-50/60 p-3 dark:border-amber-700/60 dark:bg-amber-900/20">
                      <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-300">
                        Redacted Output Preview
                      </div>
                      <pre className="max-h-48 overflow-auto text-[11px] text-amber-900 dark:text-amber-100">
{pretty(verdict.redacted_output)}
                      </pre>
                    </div>
                  )}
                </div>
              )}

              <div className="rounded-xl border border-slate-200 bg-slate-950 p-3 dark:border-slate-700">
                <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-300">
                  Raw {result.mode === MODE_DRYRUN ? "Dry-Run Response" : "Tool-Call Response"}
                </div>
                <pre className="max-h-72 overflow-auto text-[11px] text-slate-200">
{pretty(result.data)}
                </pre>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default MCPGuardrailSimulator;
