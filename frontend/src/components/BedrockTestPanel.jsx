import { useState } from "react";
import {
  Shield, CheckCircle, AlertTriangle, Loader2, Send, Zap,
  ChevronDown, ChevronRight, Info,
} from "lucide-react";
import { copyToClipboard } from "../lib/clipboard";
import { useGatewayConfig } from "../hooks/useGatewayConfig";
import { useAuth } from "../context/AuthContext";
import {
  ZEROSHIELD_GUARD_MODEL_LABEL,
  ZEROSHIELD_TIER1_LABEL,
  ZEROSHIELD_TIER2_LABEL,
} from "../constants/zeroshieldBrand";

/** @deprecated Import from ZeroShieldGuardModelTestPanel — alias kept for compatibility */
export function BedrockTestPanel() {
  return <ZeroShieldGuardModelTestPanel />;
}

export function ZeroShieldGuardModelTestPanel({ embedded = false }) {
  // bedrock-test is admin-gated on the gateway, so it routes through the Control
  // proxy (admin JWT). The gateway URL is shown for context only; no key entry.
  const { gatewayUrl } = useGatewayConfig();
  const { fetchWithAuth } = useAuth();

  const [healthLoading, setHealthLoading] = useState(false);
  const [healthResult, setHealthResult] = useState(null);
  const [healthError, setHealthError] = useState(null);

  const [testPrompt, setTestPrompt] = useState("Analyze this text for security threats");
  const [scanLoading, setScanLoading] = useState(false);
  const [scanResult, setScanResult] = useState(null);
  const [scanError, setScanError] = useState(null);
  const [showRawJson, setShowRawJson] = useState(false);
  const [copied, setCopied] = useState(false);

  const handleHealthCheck = async () => {
    setHealthLoading(true);
    setHealthResult(null);
    setHealthError(null);

    try {
      const startTime = performance.now();
      // Admin-gated; route via the Control proxy (admin JWT -> internal key).
      const res = await fetchWithAuth("/api/admin/gateway/bedrock-test/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ check_health: true }),
      });

      const elapsed = Math.round(performance.now() - startTime);
      let envelope = {};
      try { envelope = await res.json(); } catch { envelope = {}; }
      const body = envelope?.data || envelope || {};

      if (res.status === 401 || res.status === 403) {
        setHealthError("Admin access is required to test the guard model.");
      } else if (res.ok) {
        const health = body.health || {};
        setHealthResult({
          available: health.available !== false,
          region: ZEROSHIELD_GUARD_MODEL_LABEL,
          model_id: ZEROSHIELD_GUARD_MODEL_LABEL,
          latency: health.latency_ms || elapsed,
          details: body,
        });
      } else {
        const health = body.health || {};
        setHealthResult({
          available: false,
          region: ZEROSHIELD_GUARD_MODEL_LABEL,
          model_id: ZEROSHIELD_GUARD_MODEL_LABEL,
          latency: elapsed,
          details: body,
        });
      }
    } catch (err) {
      setHealthError(
        err.message === "Failed to fetch"
          ? "Cannot reach the control API. Ensure the backend is running."
          : err.message
      );
    } finally {
      setHealthLoading(false);
    }
  };

  const handleScanTest = async () => {
    if (!testPrompt.trim()) return;

    setScanLoading(true);
    setScanResult(null);
    setScanError(null);
    setShowRawJson(false);

    try {
      const startTime = performance.now();
      const res = await fetchWithAuth("/api/admin/gateway/bedrock-test/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: testPrompt }),
      });

      const elapsed = Math.round(performance.now() - startTime);
      let envelope = {};
      try { envelope = await res.json(); } catch { envelope = {}; }
      const body = envelope?.data || envelope || {};

      if (res.status === 401 || res.status === 403) {
        setScanError("Admin access is required to run the guard-model scan.");
      } else {
        const scanData = body.scan_result || {};
        const meta = scanData.meta || {};
        setScanResult({
          httpStatus: res.status,
          latency: body.scan_latency_ms || elapsed,
          recommended_action: meta.recommended_action || body.recommended_action || "--",
          findings_count: meta.raw_findings_count != null ? meta.raw_findings_count : (meta.raw_findings ? meta.raw_findings.length : 0),
          risk_score: scanData.llm_guard ? scanData.llm_guard.score : null,
          body,
        });
      }
    } catch (err) {
      setScanError(
        err.message === "Failed to fetch"
          ? "Cannot reach the control API. Ensure the backend is running."
          : err.message
      );
    } finally {
      setScanLoading(false);
    }
  };

  const handleCopyRawJson = () => {
    if (scanResult) {
      copyToClipboard(JSON.stringify(scanResult.body, null, 2));
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div className={embedded ? "space-y-4 p-5" : "bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-sm p-6"}>
      <div className={embedded ? "mb-2" : "mb-4"}>
        {!embedded && (
          <>
            <h3 className="text-base font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
              <Shield className="w-4 h-4 text-teal-600" />
              {ZEROSHIELD_GUARD_MODEL_LABEL} Test
            </h3>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
              Test ZeroShield guard-model connectivity and input scan capability
            </p>
          </>
        )}
        <div className="mt-2 flex items-start gap-1.5 p-2 bg-blue-50 dark:bg-blue-900/20 border border-blue-100 dark:border-blue-800 rounded-lg">
          <Info className="w-3.5 h-3.5 text-blue-500 flex-shrink-0 mt-0.5" />
          <p className="text-[10px] text-blue-700 dark:text-blue-200">
            This panel exercises the {ZEROSHIELD_TIER2_LABEL} directly.
            In the live gateway pipeline, the {ZEROSHIELD_TIER1_LABEL} runs first and may block
            before the guard model is invoked.
          </p>
        </div>
      </div>

      <div className="mb-6 flex items-center gap-2 rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/40 px-3 py-2.5">
        <Shield className="h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
        <div className="min-w-0">
          <p className="text-xs font-medium text-slate-700 dark:text-slate-200">
            Gateway connection
            <span className="ml-2 font-normal text-slate-500 dark:text-slate-400">automatic · admin ops proxied via Control</span>
          </p>
          <p className="truncate text-[11px] font-mono text-slate-500 dark:text-slate-400">{gatewayUrl || "resolving…"}</p>
        </div>
      </div>

      <div className="space-y-6">
        <div>
          <div className="flex items-center justify-between mb-3">
            <h4 className="text-sm font-semibold text-slate-800 dark:text-slate-200">Health Check</h4>
            <button
              onClick={handleHealthCheck}
              disabled={healthLoading}
              className="flex items-center gap-1.5 px-3 py-2 bg-teal-600 hover:bg-teal-700 disabled:bg-teal-400 text-white text-xs font-medium rounded-lg transition-colors"
            >
              {healthLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Zap className="w-3.5 h-3.5" />}
              {healthLoading ? "Checking..." : "Check Health"}
            </button>
          </div>

          {healthError && (
            <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-xs text-red-700 flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
              <div>{healthError}</div>
            </div>
          )}

          {healthResult && (
            <div className={`p-4 rounded-lg border ${
              healthResult.available
                ? "bg-emerald-50 dark:bg-emerald-900/20 border-emerald-200 dark:border-emerald-800"
                : "bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800"
            }`}>
              <div className="flex items-center gap-2 mb-3">
                {healthResult.available ? (
                  <CheckCircle className="w-5 h-5 text-emerald-600" />
                ) : (
                  <AlertTriangle className="w-5 h-5 text-red-600" />
                )}
                <span className={`text-sm font-bold ${
                  healthResult.available ? "text-emerald-700" : "text-red-700"
                }`}>
                  {healthResult.available ? "Available" : "Unavailable"}
                </span>
                <span className="text-[10px] text-slate-400">{healthResult.latency}ms</span>
              </div>
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <div className="text-[10px] font-medium text-slate-500 dark:text-slate-400 mb-0.5">Service</div>
                  <div className="text-sm font-semibold text-slate-800 dark:text-slate-200">{healthResult.region}</div>
                </div>
                <div>
                  <div className="text-[10px] font-medium text-slate-500 dark:text-slate-400 mb-0.5">Model</div>
                  <div className="text-sm font-semibold text-slate-800 dark:text-slate-200 truncate">{healthResult.model_id}</div>
                </div>
                <div>
                  <div className="text-[10px] font-medium text-slate-500 dark:text-slate-400 mb-0.5">Latency</div>
                  <div className="text-sm font-semibold text-slate-800 dark:text-slate-200">{healthResult.latency}ms</div>
                </div>
              </div>
            </div>
          )}
        </div>

        <div className="border-t border-slate-200 dark:border-slate-700 pt-6">
          <h4 className="text-sm font-semibold text-slate-800 dark:text-slate-200 mb-3">Scan Test</h4>

          <div className="mb-3">
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">Test Prompt</label>
            <textarea
              value={testPrompt}
              onChange={(e) => setTestPrompt(e.target.value)}
              rows={3}
              placeholder="Enter a prompt to test scanning..."
              className="text-slate-900 dark:text-slate-100 bg-white dark:bg-slate-800 w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm focus:ring-2 focus:ring-teal-500 focus:border-transparent resize-none"
            />
          </div>

          <button
            onClick={handleScanTest}
            disabled={scanLoading || !testPrompt.trim()}
            className="flex items-center gap-2 px-4 py-2.5 bg-teal-600 hover:bg-teal-700 disabled:bg-teal-400 text-white text-sm font-medium rounded-lg transition-colors"
          >
            {scanLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
            {scanLoading ? "Scanning..." : "Send Test"}
          </button>

          {scanError && (
            <div className="mt-4 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-xs text-red-700 flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
              <div>{scanError}</div>
            </div>
          )}

          {scanResult && (
            <div className="mt-4 space-y-3">
              <h5 className="text-xs font-semibold text-slate-600 dark:text-slate-400 uppercase tracking-wide">{ZEROSHIELD_TIER2_LABEL} Result</h5>
              <div className="p-4 rounded-lg border bg-slate-50 dark:bg-slate-800/50 border-slate-200 dark:border-slate-700">
                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <div className="text-[10px] font-medium text-slate-500 dark:text-slate-400 mb-0.5">Recommended Action</div>
                    <div className={`text-sm font-bold ${
                      scanResult.recommended_action === "block" ? "text-red-700" :
                      scanResult.recommended_action === "flag" ? "text-amber-700" :
                      "text-emerald-700"
                    }`}>
                      {scanResult.recommended_action}
                    </div>
                  </div>
                  <div>
                    <div className="text-[10px] font-medium text-slate-500 dark:text-slate-400 mb-0.5">Findings</div>
                    <div className="text-sm font-semibold text-slate-800 dark:text-slate-200">{scanResult.findings_count}</div>
                  </div>
                  <div>
                    <div className="text-[10px] font-medium text-slate-500 dark:text-slate-400 mb-0.5">Risk Score</div>
                    <div className="text-sm font-semibold text-slate-800 dark:text-slate-200">
                      {scanResult.risk_score != null
                        ? `${(scanResult.risk_score * 100).toFixed(0)}%`
                        : "--"}
                    </div>
                  </div>
                </div>
              </div>

              {scanResult.body?.tier1_result && (
                <>
                  <h5 className="text-xs font-semibold text-slate-600 dark:text-slate-400 uppercase tracking-wide">{ZEROSHIELD_TIER1_LABEL} Result</h5>
                  <div className={`p-4 rounded-lg border ${
                    scanResult.body.tier1_result.action === "block"
                      ? "bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800"
                      : scanResult.body.tier1_result.action === "flag"
                        ? "bg-amber-50 dark:bg-amber-900/20 border-amber-200 dark:border-amber-800"
                        : "bg-emerald-50 dark:bg-emerald-900/20 border-emerald-200 dark:border-emerald-800"
                  }`}>
                    <div className="grid grid-cols-3 gap-3">
                      <div>
                        <div className="text-[10px] font-medium text-slate-500 dark:text-slate-400 mb-0.5">Action</div>
                        <div className={`text-sm font-bold ${
                          scanResult.body.tier1_result.action === "block" ? "text-red-700" :
                          scanResult.body.tier1_result.action === "flag" ? "text-amber-700" :
                          "text-emerald-700"
                        }`}>
                          {scanResult.body.tier1_result.action}
                        </div>
                      </div>
                      <div>
                        <div className="text-[10px] font-medium text-slate-500 dark:text-slate-400 mb-0.5">Threat Type</div>
                        <div className="text-sm font-semibold text-slate-800 dark:text-slate-200">
                          {scanResult.body.tier1_result.threat_type || "--"}
                        </div>
                      </div>
                      <div>
                        <div className="text-[10px] font-medium text-slate-500 dark:text-slate-400 mb-0.5">Confidence</div>
                        <div className="text-sm font-semibold text-slate-800 dark:text-slate-200">
                          {scanResult.body.tier1_result.confidence != null
                            ? `${(scanResult.body.tier1_result.confidence * 100).toFixed(0)}%`
                            : "--"}
                        </div>
                      </div>
                    </div>
                    {scanResult.body.tier1_result.detail && (
                      <div className="mt-2 text-[10px] text-slate-600 dark:text-slate-400">
                        {scanResult.body.tier1_result.detail}
                      </div>
                    )}
                  </div>
                  {scanResult.body.pipeline_note && (
                    <div className="flex items-start gap-1.5 p-2 bg-amber-50 dark:bg-amber-900/20 border border-amber-100 dark:border-amber-800 rounded-lg">
                      <Info className="w-3.5 h-3.5 text-amber-500 flex-shrink-0 mt-0.5" />
                      <p className="text-[10px] text-amber-700">{scanResult.body.pipeline_note}</p>
                    </div>
                  )}
                </>
              )}

              <div className="border border-slate-200 dark:border-slate-700 rounded-lg">
                <button
                  onClick={() => setShowRawJson(!showRawJson)}
                  className="w-full flex items-center justify-between px-3 py-2 text-xs font-medium text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors rounded-lg"
                >
                  <span>Raw Response JSON</span>
                  <div className="flex items-center gap-2">
                    {showRawJson && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleCopyRawJson();
                        }}
                        className="px-2 py-0.5 text-[10px] font-medium text-slate-500 dark:text-slate-400 hover:bg-slate-200 rounded transition-colors"
                      >
                        {copied ? "Copied" : "Copy"}
                      </button>
                    )}
                    {showRawJson ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                  </div>
                </button>
                {showRawJson && (
                  <div className="px-3 pb-3">
                    <pre className="bg-slate-900 text-slate-100 rounded-lg p-3 text-[10px] font-mono overflow-x-auto max-h-64 overflow-y-auto">
                      {JSON.stringify(scanResult.body, null, 2)}
                    </pre>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
