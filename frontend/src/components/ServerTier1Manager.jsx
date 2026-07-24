/**
 * ServerTier1Manager — the per-server "Manage Tier-1" surface.
 *
 * Shows EVERY enabled MCP policy in the org (org-wide + server-bound) with its
 * EFFECTIVE state for ONE server, and lets the operator enable/disable each
 * policy AND each of its rules FOR THAT SERVER without touching the policies
 * themselves. Every toggle is a per-server override:
 *
 *   PUT    /api/policies/mcp-server-states/  { server_id, policy_id, enabled }
 *   PUT    /api/policies/mcp-server-states/  { server_id, rule_id,   enabled }
 *   DELETE /api/policies/mcp-server-states/  { server_id, policy_id | rule_id }  (revert to default)
 *
 * The GET reflects the same policies the Policy Management page edits, so this
 * view and that page stay in sync.
 */

import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, RotateCcw, ShieldOff } from "lucide-react";

import { Badge } from "./ui/Badge";
import { Spinner } from "./ui/Spinner";
import { Switch } from "./ui/Switch";
import { useToast } from "./ui/Toast";

// Rule action → Badge variant. Unknown/absent actions fall back to a neutral
// outline so a new backend action never crashes the row.
const ACTION_VARIANT = {
  block: "danger",
  redact: "warning",
  monitor: "info",
  flag: "info",
  allow: "success",
  pass: "success",
};

function ActionBadge({ action }) {
  if (!action) return null;
  return (
    <Badge variant={ACTION_VARIANT[String(action).toLowerCase()] || "outline"} className="uppercase">
      {action}
    </Badge>
  );
}

/** "org-wide" or "bound: <slug>" provenance badge for a policy. */
function ScopeBadge({ orgWide, slug }) {
  if (orgWide) {
    return <Badge variant="secondary">org-wide</Badge>;
  }
  return (
    <Badge variant="outline" className="font-mono">
      bound: {slug || "—"}
    </Badge>
  );
}

export function ServerTier1Manager({ server, fetchWithAuth, onOpenPolicyManagement }) {
  const { toast } = useToast();

  const [policies, setPolicies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [expanded, setExpanded] = useState(() => new Set()); // policy_ids
  // Keys of in-flight mutations ("policy:<id>" / "rule:<id>") so we can disable
  // just that control and show a per-row spinner while it saves.
  const [saving, setSaving] = useState(() => new Set());

  const serverId = server?.id;

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchWithAuth(
        `/api/policies/mcp-server-states/?server_id=${encodeURIComponent(serverId)}`
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setPolicies(Array.isArray(data?.policies) ? data.policies : []);
    } catch (e) {
      setError(e.message || "Failed to load policy states");
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth, serverId]);

  useEffect(() => {
    load();
  }, [load]);

  const isSaving = (key) => saving.has(key);
  const addSaving = (key) =>
    setSaving((s) => {
      const next = new Set(s);
      next.add(key);
      return next;
    });
  const dropSaving = (key) =>
    setSaving((s) => {
      const next = new Set(s);
      next.delete(key);
      return next;
    });

  // Immutable helpers to patch one policy / one rule in local state.
  const patchPolicy = (policyId, updater) =>
    setPolicies((prev) =>
      prev.map((p) => (String(p.policy_id) === String(policyId) ? { ...p, ...updater(p) } : p))
    );

  const patchRule = (policyId, ruleId, updater) =>
    setPolicies((prev) =>
      prev.map((p) => {
        if (String(p.policy_id) !== String(policyId)) return p;
        return {
          ...p,
          rules: (p.rules || []).map((r) =>
            String(r.rule_id) === String(ruleId) ? { ...r, ...updater(r) } : r
          ),
        };
      })
    );

  /* ── writes (optimistic + rollback) ── */

  const put = async (body) => {
    const res = await fetchWithAuth("/api/policies/mcp-server-states/", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res;
  };

  const del = async (body) => {
    // fetchWithAuth only auto-sets Content-Type for POST/PUT/PATCH, so a DELETE
    // with a JSON body must set it explicitly.
    const res = await fetchWithAuth("/api/policies/mcp-server-states/", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok && res.status !== 204) throw new Error(`HTTP ${res.status}`);
    return res;
  };

  const togglePolicy = async (policy, next) => {
    const key = `policy:${policy.policy_id}`;
    if (isSaving(key)) return;
    const prev = { enabled_for_server: policy.enabled_for_server, overridden: policy.overridden };
    addSaving(key);
    // Optimistic: any explicit PUT creates a per-server override row.
    patchPolicy(policy.policy_id, () => ({ enabled_for_server: next, overridden: true }));
    try {
      await put({ server_id: serverId, policy_id: policy.policy_id, enabled: next });
    } catch (e) {
      patchPolicy(policy.policy_id, () => prev); // rollback
      toast(e.message || "Failed to update policy", { tone: "error" });
    } finally {
      dropSaving(key);
    }
  };

  const toggleRule = async (policy, rule, next) => {
    const key = `rule:${rule.rule_id}`;
    if (isSaving(key)) return;
    const prev = { enabled_for_server: rule.enabled_for_server, overridden: rule.overridden };
    addSaving(key);
    patchRule(policy.policy_id, rule.rule_id, () => ({ enabled_for_server: next, overridden: true }));
    try {
      await put({ server_id: serverId, rule_id: rule.rule_id, enabled: next });
    } catch (e) {
      patchRule(policy.policy_id, rule.rule_id, () => prev); // rollback
      toast(e.message || "Failed to update rule", { tone: "error" });
    } finally {
      dropSaving(key);
    }
  };

  const revertPolicy = async (policy) => {
    const key = `policy:${policy.policy_id}`;
    if (isSaving(key)) return;
    addSaving(key);
    try {
      await del({ server_id: serverId, policy_id: policy.policy_id });
      await load(); // reconcile to the true default state
    } catch (e) {
      toast(e.message || "Failed to revert policy", { tone: "error" });
    } finally {
      dropSaving(key);
    }
  };

  const revertRule = async (policy, rule) => {
    const key = `rule:${rule.rule_id}`;
    if (isSaving(key)) return;
    addSaving(key);
    try {
      await del({ server_id: serverId, rule_id: rule.rule_id });
      await load(); // reconcile to the true default state
    } catch (e) {
      toast(e.message || "Failed to revert rule", { tone: "error" });
    } finally {
      dropSaving(key);
    }
  };

  const toggleExpand = (policyId) =>
    setExpanded((s) => {
      const next = new Set(s);
      if (next.has(policyId)) next.delete(policyId);
      else next.add(policyId);
      return next;
    });

  /* ── render ── */

  if (loading) {
    return (
      <div className="flex items-center justify-center gap-2 py-10 text-sm text-slate-500 dark:text-slate-400">
        <Spinner size="sm" className="text-teal-500" />
        Loading policies…
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/20 p-4">
        <p className="text-sm text-red-700 dark:text-red-300">{error}</p>
        <button
          type="button"
          onClick={load}
          className="mt-3 rounded-lg bg-red-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-red-700"
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* header */}
      <div className="rounded-lg border border-slate-200/80 dark:border-slate-700 bg-slate-50/70 dark:bg-slate-800/40 px-4 py-3">
        <p className="text-sm text-slate-600 dark:text-slate-300">
          Enable or disable each policy and rule for{" "}
          <span className="font-semibold text-slate-900 dark:text-slate-100">{server.name}</span>. This
          does not change the policies themselves — edit those in Policy Management.
        </p>
        {onOpenPolicyManagement && (
          <button
            type="button"
            onClick={() => onOpenPolicyManagement()}
            className="mt-2 text-xs font-medium text-teal-600 dark:text-teal-400 hover:underline"
          >
            Open Policy Management →
          </button>
        )}
      </div>

      {policies.length === 0 ? (
        <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-slate-200 dark:border-slate-700 py-10 text-center">
          <ShieldOff className="h-6 w-6 text-slate-400" aria-hidden="true" />
          <p className="text-sm text-slate-500 dark:text-slate-400">
            No MCP policies exist yet. Create them in Policy Management.
          </p>
        </div>
      ) : (
        <ul className="space-y-2">
          {policies.map((policy) => {
            const pKey = `policy:${policy.policy_id}`;
            const pSaving = isSaving(pKey);
            const isOpen = expanded.has(policy.policy_id);
            const rules = Array.isArray(policy.rules) ? policy.rules : [];
            return (
              <li
                key={policy.policy_id}
                className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/40"
              >
                {/* policy row */}
                <div className="flex items-center gap-3 px-4 py-3">
                  <button
                    type="button"
                    onClick={() => toggleExpand(policy.policy_id)}
                    aria-expanded={isOpen}
                    aria-label={isOpen ? `Collapse ${policy.name} rules` : `Expand ${policy.name} rules`}
                    className="shrink-0 rounded p-0.5 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"
                    disabled={rules.length === 0}
                  >
                    {rules.length === 0 ? (
                      <ChevronRight className="h-4 w-4 opacity-30" aria-hidden="true" />
                    ) : isOpen ? (
                      <ChevronDown className="h-4 w-4" aria-hidden="true" />
                    ) : (
                      <ChevronRight className="h-4 w-4" aria-hidden="true" />
                    )}
                  </button>

                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="truncate text-sm font-semibold text-slate-900 dark:text-slate-100">
                        {policy.name}
                      </span>
                      <code className="rounded bg-slate-100 dark:bg-slate-700/60 px-1.5 py-0.5 font-mono text-[11px] text-slate-600 dark:text-slate-300">
                        {policy.code}
                      </code>
                      <ScopeBadge orgWide={policy.org_wide} slug={policy.mcp_server_slug} />
                      {policy.overridden && (
                        <Badge variant="warning" className="normal-case">
                          overridden
                        </Badge>
                      )}
                    </div>
                    <p className="mt-0.5 text-[11px] text-slate-500 dark:text-slate-400">
                      {rules.length} rule{rules.length === 1 ? "" : "s"}
                      {" · "}
                      {policy.enabled_for_server ? "enabled" : "disabled"} for this server
                    </p>
                  </div>

                  <div className="flex shrink-0 items-center gap-2">
                    {policy.overridden && (
                      <button
                        type="button"
                        onClick={() => revertPolicy(policy)}
                        disabled={pSaving}
                        title="Revert to default (clear this server's override)"
                        aria-label={`Revert ${policy.name} to default`}
                        className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] font-medium text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        <RotateCcw className="h-3 w-3" aria-hidden="true" />
                        Revert
                      </button>
                    )}
                    {pSaving && <Spinner size="sm" className="h-4 w-4 text-teal-500" />}
                    <Switch
                      checked={!!policy.enabled_for_server}
                      disabled={pSaving}
                      onCheckedChange={(v) => togglePolicy(policy, v)}
                      label={`${policy.enabled_for_server ? "Disable" : "Enable"} policy ${policy.name} for ${server.name}`}
                    />
                  </div>
                </div>

                {/* rules */}
                {isOpen && rules.length > 0 && (
                  <ul className="space-y-1 border-t border-slate-200 dark:border-slate-700 px-4 py-3">
                    {rules.map((rule) => {
                      const rKey = `rule:${rule.rule_id}`;
                      const rSaving = isSaving(rKey);
                      return (
                        <li
                          key={rule.rule_id}
                          className="flex items-center gap-3 rounded-lg bg-slate-50 dark:bg-slate-900/50 px-3 py-2"
                        >
                          <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="truncate text-xs font-medium text-slate-700 dark:text-slate-300">
                                {rule.name}
                              </span>
                              <ActionBadge action={rule.action} />
                              {rule.overridden && (
                                <Badge variant="warning" className="normal-case">
                                  overridden
                                </Badge>
                              )}
                            </div>
                          </div>
                          <div className="flex shrink-0 items-center gap-2">
                            {rule.overridden && (
                              <button
                                type="button"
                                onClick={() => revertRule(policy, rule)}
                                disabled={rSaving}
                                title="Revert to default (clear this server's override)"
                                aria-label={`Revert rule ${rule.name} to default`}
                                className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] font-medium text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 disabled:opacity-40 disabled:cursor-not-allowed"
                              >
                                <RotateCcw className="h-3 w-3" aria-hidden="true" />
                                Revert
                              </button>
                            )}
                            {rSaving && <Spinner size="sm" className="h-3.5 w-3.5 text-teal-500" />}
                            <Switch
                              checked={!!rule.enabled_for_server}
                              disabled={rSaving}
                              onCheckedChange={(v) => toggleRule(policy, rule, v)}
                              label={`${rule.enabled_for_server ? "Disable" : "Enable"} rule ${rule.name} for ${server.name}`}
                            />
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
