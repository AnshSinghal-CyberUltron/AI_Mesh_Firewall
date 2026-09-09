/**
 * Tier2ScanControl — the org-level enable/disable control for the chat/input
 * Tier-2 (ZeroShield model) semantic scan, bound to the ``tier2_enabled``
 * firewall-config key.
 *
 * Tier-2 is opt-in and default-OFF (policy-driven-detection Req 3). The stored
 * value is a NULLABLE TRI-STATE — null=Inherit gateway default, true=Enabled,
 * false=Disabled — so this renders a three-way SegmentedControl, mirroring the
 * MCP org Tier-2 master (MCPScanControlMatrix.saveTier2Master).
 *
 * It reflects the current backend value on mount and, on change, PUTs ONLY the
 * changed field ({tier2_enabled: value}) to the control-plane firewall-config
 * API (which syncs to the gateway). The partial PUT is deliberate: re-sending
 * the whole config re-validates unrelated siblings (e.g. a stale allowed_models
 * list) and can 400 for reasons unrelated to Tier-2 (see CHG-0028).
 */

import { useCallback, useEffect, useState } from "react";
import { Sparkles } from "lucide-react";

import { useAuth } from "../../context/AuthContext";
import { Badge } from "../ui/Badge";
import { Spinner } from "../ui/Spinner";
import { SegmentedControl } from "../ui/SegmentedControl";
import { useToast } from "../ui/Toast";
import { ZEROSHIELD_TIER2_LABEL } from "../../constants/zeroshieldBrand";
import {
  resolveTier2SegmentValue,
  buildTier2PutPayload,
} from "../../utils/tier2ScanControl";

const TIER2_OPTIONS = [
  { value: "inherit", label: "Inherit" },
  { value: "enabled", label: "Enabled" },
  { value: "disabled", label: "Disabled" },
];

export function Tier2ScanControl() {
  const { fetchWithAuth } = useAuth();
  const { toast } = useToast();

  // undefined = not loaded yet; null = Inherit; true/false = explicit override.
  const [tier2, setTier2] = useState(undefined);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const res = await fetchWithAuth("/api/firewall/config/");
      if (!res.ok) return;
      const data = await res.json();
      // Preserve the tri-state: an absent key is Inherit (null), not false.
      setTier2(data.tier2_enabled ?? null);
    } catch {
      /* non-blocking; keep the last known state */
    }
  }, [fetchWithAuth]);

  useEffect(() => {
    load();
  }, [load]);

  const save = async (value) => {
    if (saving) return;
    setSaving(true);
    setError(null);
    const prev = tier2;
    setTier2(value); // optimistic
    try {
      // Partial PUT — only the changed field (see CHG-0028).
      const res = await fetchWithAuth("/api/firewall/config/", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildTier2PutPayload(resolveTier2SegmentValue(value))),
      });
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const err = await res.json();
          detail = err?.tier2_enabled?.[0] || err?.detail || detail;
        } catch {
          /* keep status */
        }
        throw new Error(detail);
      }
      const updated = await res.json().catch(() => ({ tier2_enabled: value }));
      // Reflect the backend value (falls back to the value we just sent).
      setTier2(updated.tier2_enabled ?? (value === null ? null : value));
      toast(
        value === null
          ? "Tier-2 set to Inherit (org default)"
          : value
            ? "Tier-2 scanning enabled for this org"
            : "Tier-2 scanning disabled for this org",
        { tone: "success" },
      );
    } catch (e) {
      setTier2(prev); // rollback
      setError(e.message);
      toast(e.message || "Failed to save Tier-2 setting", { tone: "error" });
    } finally {
      setSaving(false);
    }
  };

  const state = resolveTier2SegmentValue(tier2);
  const stateLabel =
    tier2 === null || tier2 === undefined
      ? "Inherit (org default)"
      : tier2
        ? "Enabled"
        : "Disabled";
  const badgeVariant =
    tier2 === null || tier2 === undefined ? "secondary" : tier2 ? "success" : "danger";

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border/60 bg-surface-2/50 px-4 py-3">
      <div className="flex min-w-0 flex-1 items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-violet-100 text-violet-600 dark:bg-violet-900/40 dark:text-violet-300">
          <Sparkles className="h-4 w-4" aria-hidden="true" />
        </div>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-foreground">
              Tier-2 ({ZEROSHIELD_TIER2_LABEL}) Scanning
            </span>
            <Badge variant={badgeVariant}>{stateLabel}</Badge>
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Opt-in semantic model scan for chat / input. Off by default — when enabled the model
            alone decides the action. Inherit defers to the gateway default.
          </p>
          {error && (
            <p role="alert" className="mt-1 text-xs text-red-600 dark:text-red-400">
              {error}
            </p>
          )}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {saving && <Spinner className="h-4 w-4 text-violet-500" />}
        <SegmentedControl
          aria-label="Tier-2 scanning mode"
          value={state}
          onChange={(v) => save(buildTier2PutPayload(v).tier2_enabled)}
          options={TIER2_OPTIONS}
        />
      </div>
    </div>
  );
}
