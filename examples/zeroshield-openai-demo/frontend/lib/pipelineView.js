/**
 * Vanilla port of LogDetailPage pipeline sections:
 * RoutingDecisionCard + StageTimeline + Input/Output + latency breakdown.
 */
import {
  resolveRoutingDecision,
  resolvePipelineInputOutput,
  resolveLatencyBreakdown,
  resolveTotalLatencyMs,
  resolveTtftMs,
  formatPipelineDurationMs,
  formatDominantStageLabel,
  formatRouteDestination,
} from "./pipelineTrace.js";
import { summarizeRoutingDecision } from "./routingExplain.js";
import { routingHasTechnicalDetails } from "./routingExplain.js";
import { formatDecisionSource } from "./zeroshieldBrand.js";
import { mountStageTimeline } from "./stageTimeline.js";

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]
  ));
}

function routingTechnicalHtml(technical) {
  if (!routingHasTechnicalDetails(technical)) return "";
  const rows = [];
  if (technical.routing_reason) {
    rows.push(`<div><span class="pst-muted">Routing reason: </span>${esc(technical.routing_reason)}</div>`);
  }
  if (technical.policy_summary) {
    rows.push(`<div><span class="pst-muted">Policy: </span>${esc(technical.policy_summary)}</div>`);
  }
  if (Array.isArray(technical.decision_factors) && technical.decision_factors.length) {
    rows.push(`<div><span class="pst-muted">Decision factors: </span><span class="pst-mono">${esc(technical.decision_factors.join(", "))}</span></div>`);
  }
  const weightEntries = technical.weights ? Object.entries(technical.weights) : [];
  if (weightEntries.length) {
    const w = weightEntries.map(([k, v]) => {
      const raw = Number(v);
      return `${k}=${Number.isFinite(raw) ? `${Math.round(raw * 100)}%` : String(v)}`;
    }).join(", ");
    rows.push(`<div><span class="pst-muted">Weights: </span>${esc(w)}</div>`);
  }
  if (Number(technical.routing_score) > 0) {
    rows.push(`<div><span class="pst-muted">Score: </span>${Number(technical.routing_score).toFixed(3)}</div>`);
  }
  if (Number(technical.candidate_count) > 0) {
    rows.push(`<div><span class="pst-muted">Candidates: </span>${esc(technical.candidate_count)}</div>`);
  }
  if (Array.isArray(technical.fallback_chain) && technical.fallback_chain.length) {
    rows.push(`<div><span class="pst-muted">Fallback chain: </span><span class="pst-mono">${esc(technical.fallback_chain.join(" → "))}</span></div>`);
  }
  return `<details class="pst-tech">
    <summary><span class="open-hide">Show technical details</span><span class="open-show">Hide technical details</span></summary>
    <div class="pst-tech-body">${rows.join("")}</div>
  </details>`;
}

function renderRoutingCard(el, routing, ctx = {}) {
  if (!el) return;
  if (!routing) {
    const msg = ctx.blockMessage || "Run a request to see routing + validation.";
    el.innerHTML = `<em>${esc(msg)}</em>`;
    return;
  }
  const { summary, technical } = summarizeRoutingDecision(routing);
  const sourceLabel = routing.decision_source_label
    || formatDecisionSource(routing.decision_source);
  const dest = routing.route_destination_label
    || formatRouteDestination(routing.route_destination);
  // Show a reason whenever the request is blocked — derive from ctx or trace fields.
  const reasonText = ctx.blockMessage
    || (ctx.blocked && (routing?.guard_reason || ""))
    || "";
  const showBlockReason = !!(ctx.blocked && reasonText);

  el.innerHTML = `
    <div class="routing-decision-card" data-testid="routing-decision-card">
      <div class="rdc-head">
        <span class="rdc-label">Routing decision</span>
        ${dest ? `<span class="rdc-dest">${esc(dest)}</span>` : ""}
      </div>
      <div class="rdc-grid">
        ${routing.requested_model ? `<div><span class="pst-muted">Requested:</span> <span class="pst-mono" data-testid="vz-requested">${esc(routing.requested_model)}</span></div>` : `<div><span class="pst-muted">Requested:</span> <span class="pst-mono" data-testid="vz-requested">auto</span></div>`}
        ${routing.routed_model || routing.selected_model
          ? `<div><span class="pst-muted">Routed to:</span> <span class="pst-mono pst-dest" data-testid="vz-selected">${esc(routing.routed_model || routing.selected_model)}</span></div>`
          : `<div><span class="pst-muted">Routed to:</span> <span class="pst-mono" data-testid="vz-selected"></span></div>`}
        ${sourceLabel ? `<div><span class="pst-muted">Decision source:</span> ${esc(sourceLabel)}</div>` : ""}
      </div>
      ${summary ? `<p class="rdc-summary">${esc(summary)}</p>` : ""}
      ${routingTechnicalHtml(technical)}
      ${showBlockReason ? `<div class="rc-reason bad" data-testid="vz-block-reason">${esc(reasonText)}</div>` : ""}
    </div>`;
}

function renderIO(el, io) {
  if (!el) return;
  if (!io || !(io.inputText || io.outputText || io.outputWithheld || io.inputWasRedacted)) {
    el.innerHTML = `<p class="st-note">No input/output payload on this trace.</p>`;
    return;
  }
  const parts = [`<h4 class="pv-section-title">Input / Output</h4>`];
  if (io.inputWasRedacted) {
    parts.push(`<div class="pv-io-block"><span class="pv-io-label">Input (before redaction)</span>
      <pre class="pst-pre block" data-testid="pipeline-io-input-before">${esc(io.inputBefore)}</pre></div>`);
    parts.push(`<div class="pv-io-block"><span class="pv-io-label">Input (forwarded to model)</span>
      <pre class="pst-pre block after" data-testid="pipeline-io-input-after">${esc(io.inputAfter)}</pre></div>`);
  } else if (io.inputText) {
    parts.push(`<div class="pv-io-block"><span class="pv-io-label">Input (prompt)</span>
      <pre class="pst-pre block" data-testid="pipeline-io-input">${esc(io.inputText)}</pre></div>`);
  }
  if (io.outputWithheld) {
    parts.push(`<div class="pv-io-block">
      <span class="pv-io-label">Output (response)</span>
      <p class="pv-withheld" data-testid="pipeline-io-withheld">${esc(io.outputWithheldReason || "[Response withheld — not delivered to client]")}</p>
      ${io.outputWithheldPreview
        ? `<span class="pv-io-label">Model output (withheld from client)</span>
           <pre class="pst-pre block" data-testid="pipeline-io-withheld-preview">${esc(io.outputWithheldPreview)}</pre>`
        : ""}
    </div>`);
  } else if (io.outputText) {
    parts.push(`<div class="pv-io-block"><span class="pv-io-label">Output (response)</span>
      <pre class="pst-pre block after" data-testid="pipeline-io-output">${esc(io.outputText)}</pre></div>`);
  }
  el.innerHTML = parts.join("");
}

function renderLatency(el, breakdown) {
  if (!el) return;
  if (!breakdown || !(breakdown.by_stage?.length || breakdown.hints?.length)) {
    el.innerHTML = "";
    return;
  }
  const total = breakdown.total_latency_ms ?? 0;
  const rows = (breakdown.by_stage || []).map((row) => {
    const pct = Number(row.share_pct) || 0;
    const lat = formatPipelineDurationMs(row.latency_ms);
    return `<tr data-testid="latency-row-${esc(row.stage)}">
      <td>${esc(formatDominantStageLabel(row.stage))}</td>
      <td class="pst-mono">${esc(lat)}</td>
      <td>
        <div class="pv-bar-track"><div class="pv-bar-fill" style="width:${Math.min(100, pct)}%"></div></div>
        <span class="pv-bar-pct">${Math.round(pct)}%</span>
      </td>
    </tr>`;
  }).join("");

  const hints = (breakdown.hints || []).map((hint) => `
    <div class="pv-hint ${hint.severity === "high" ? "high" : ""}" data-testid="latency-hint-${esc(hint.stage)}">
      <p>${esc(hint.message)}</p>
      ${Array.isArray(hint.actions) && hint.actions.length
        ? `<ul>${hint.actions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>`
        : ""}
    </div>`).join("");

  el.innerHTML = `
    <h4 class="pv-section-title">Latency Breakdown</h4>
    ${breakdown.dominant_stage
      ? `<p class="pv-dominant" data-testid="latency-dominant">Dominant stage:
          <b>${esc(formatDominantStageLabel(breakdown.dominant_stage))}</b>
          ${breakdown.dominant_latency_ms != null
            ? ` — ${esc(formatPipelineDurationMs(breakdown.dominant_latency_ms))}${
              breakdown.dominant_share_pct != null
                ? ` (${Math.round(breakdown.dominant_share_pct)}% of total)`
                : ""
            }`
            : ""}
        </p>`
      : ""}
    ${rows ? `<table class="pv-lat-table" data-testid="pipeline-latency-table">
      <thead><tr><th>Stage</th><th>Latency</th><th>Share</th></tr></thead>
      <tbody>${rows}</tbody>
      <tfoot><tr><td>Total</td><td class="pst-mono" data-testid="pipeline-latency-total">${esc(formatPipelineDurationMs(total))}</td><td></td></tr></tfoot>
    </table>` : ""}
    ${hints ? `<div class="pv-hints"><h5>How to reduce latency</h5>${hints}</div>` : ""}
  `;
}

function renderMetrics(el, sources, action) {
  if (!el) return;
  const total = resolveTotalLatencyMs(sources);
  const ttft = resolveTtftMs(sources);
  el.innerHTML = `
    <div class="pv-metrics" data-testid="pipeline-metrics">
      <div class="pv-metric"><span class="pv-metric-label">Overall Status</span>
        <b class="action-${esc(String(action || "allow").toLowerCase())}" data-testid="pipeline-metric-status">${esc(String(action || "—").toUpperCase())}</b></div>
      <div class="pv-metric"><span class="pv-metric-label">Total Duration</span>
        <b data-testid="pipeline-metric-duration">${esc(formatPipelineDurationMs(total))}</b></div>
      ${ttft != null ? `<div class="pv-metric"><span class="pv-metric-label">Time to First Token</span>
        <b data-testid="pipeline-metric-ttft">${esc(formatPipelineDurationMs(ttft))}</b></div>` : ""}
    </div>`;
}

let _timelineHandle = null;

/**
 * Build a pipelineTrace-shaped object from the demo summarize_trace() envelope.
 */
export function sourcesFromDemoTrace(trace) {
  if (!trace) return { pipelineTrace: null };
  const pt = trace.pipeline_trace && typeof trace.pipeline_trace === "object"
    ? { ...trace.pipeline_trace }
    : {};
  if (!Array.isArray(pt.stages) || !pt.stages.length) {
    pt.stages = Array.isArray(trace.stages) ? trace.stages : [];
  }
  if (!pt.routing && trace.routing) {
    pt.routing = {
      requested_model: trace.routing.requested_model || trace.routing.requested,
      selected_model: trace.routing.selected_model || trace.routing.selected,
      routed_model: trace.routing.routed_model || trace.routing.selected,
      routing_reason: trace.routing.routing_reason || trace.routing.reason,
      decision_source: trace.routing.decision_source,
      decision_source_label: trace.routing.decision_source_label,
      policy_summary: trace.routing.policy_summary,
      decision_factors: trace.routing.decision_factors,
      weights: trace.routing.weights,
      routing_score: trace.routing.routing_score,
      candidate_count: trace.routing.candidate_count,
      fallback_chain: trace.routing.fallback_chain,
      route_destination: trace.routing.route_destination,
      route_destination_label: trace.routing.route_destination_label,
      rerouted: trace.routing.rerouted,
    };
  }
  if (pt.final_action == null && trace.final_action) pt.final_action = trace.final_action;
  if (pt.final_action == null && trace.action) pt.final_action = trace.action;
  if (pt.total_latency_ms == null && trace.total_latency_ms != null) {
    pt.total_latency_ms = trace.total_latency_ms;
  }
  if (pt.total_latency_ms == null && trace.processing_time_ms != null) {
    pt.total_latency_ms = trace.processing_time_ms;
  }
  if (pt.ttft_ms == null && trace.ttft_ms != null) pt.ttft_ms = trace.ttft_ms;
  return {
    pipelineTrace: pt,
    fallbackAction: trace.action,
  };
}

/**
 * Render the full pipeline view into the demo DOM anchors.
 * Preserves #vz-route, #vz-stages, #vz-meta, #vz-obs testids.
 */
export function renderPipelineView(trace, ctx = {}, emptyStagesReasonFn) {
  const routeEl = document.querySelector("#vz-route");
  const stagesEl = document.querySelector("#vz-stages");
  const metaEl = document.querySelector("#vz-meta");
  const metricsEl = document.querySelector("#pipeline-metrics-host");
  const ioEl = document.querySelector("#pipeline-io");
  const latEl = document.querySelector("#pipeline-latency");
  const traceView = document.querySelector("#pipeline-trace-view");

  if (_timelineHandle) {
    _timelineHandle.destroy();
    _timelineHandle = null;
  }

  if (!trace) {
    // Pending/idle reset — clear previous request's cards immediately.
    if (ctx.pending || ctx.idle) {
      const msg = ctx.pendingMessage
        || (ctx.pending ? "Running request…" : "Run a request to see routing + validation.");
      if (routeEl) {
        routeEl.innerHTML = `<em data-testid="${ctx.pending ? "vz-pending" : "vz-idle"}">${esc(msg)}</em>`;
      }
      if (stagesEl) stagesEl.innerHTML = "";
      if (metaEl) metaEl.innerHTML = ctx.pending
        ? `<span class="vz-pending-meta" data-testid="vz-pending-meta">waiting for pipeline…</span>`
        : "";
      if (metricsEl) metricsEl.innerHTML = "";
      if (ioEl) ioEl.innerHTML = "";
      if (latEl) latEl.innerHTML = "";
      return;
    }
    const blockedMsg =
      ctx.blockMessage ||
      ctx.zeroshieldDetail ||
      (ctx.blocked
        ? "Request blocked — gateway returned no pipeline_trace on this response."
        : "No trace returned for this request.");
    if (routeEl) {
      routeEl.innerHTML = `<em data-testid="vz-empty-reason">${esc(blockedMsg)}</em>`;
    }
    if (stagesEl) {
      stagesEl.innerHTML = ctx.blocked
        ? `<div class="st-note" data-testid="vz-block-reason">${esc(blockedMsg)}</div>`
        : "";
    }
    if (metaEl) {
      metaEl.innerHTML = ctx.blocked
        ? `verdict: <b class="action-block">block</b>`
        : "";
    }
    if (metricsEl) metricsEl.innerHTML = "";
    if (ioEl) ioEl.innerHTML = "";
    if (latEl) latEl.innerHTML = "";
    return;
  }

  const sources = sourcesFromDemoTrace(trace);
  const blocked =
    ctx.blocked ||
    trace.action === "block" ||
    (typeof ctx.blockMessage === "string" && ctx.blockMessage.length > 0);
  const blockMessage = ctx.blockMessage
    || (blocked
      ? (trace.guard_reason || trace.detail
        || (trace.threat_type
          ? `Request blocked (${String(trace.threat_type).replace(/_/g, " ")})`
          : "Request blocked due to security policy"))
      : undefined);
  const viewCtx = { ...ctx, blocked, blockMessage };

  // Prefer resolved routing from full trace; fall back to compact routing for gates.
  let routing = resolveRoutingDecision(sources);
  if (!routing && trace.routing) {
    routing = {
      requested_model: trace.routing.requested || trace.routing.requested_model || "auto",
      selected_model: trace.routing.selected || trace.routing.selected_model || "",
      routed_model: trace.routing.selected || trace.routing.routed_model || "",
      routing_reason: trace.routing.reason || trace.routing.routing_reason || "",
      decision_source: trace.routing.decision_source || "",
      decision_source_label: trace.routing.decision_source_label || "",
      weights: trace.routing.weights,
      rerouted: trace.routing.rerouted,
      route_destination: trace.routing.route_destination || "llm",
      route_destination_label: trace.routing.route_destination_label || "",
      policy_summary: trace.routing.policy_summary || "",
      decision_factors: trace.routing.decision_factors || [],
      routing_score: trace.routing.routing_score || 0,
      candidate_count: trace.routing.candidate_count || 0,
      fallback_chain: trace.routing.fallback_chain || [],
    };
  }

  renderRoutingCard(routeEl, routing, viewCtx);
  renderMetrics(metricsEl, sources, trace.action || sources.pipelineTrace?.final_action);

  const stages = sources.pipelineTrace?.stages || trace.stages || [];
  if (!stages.length) {
    const reasonFn = emptyStagesReasonFn || (() => "No pipeline stages returned for this response.");
    if (stagesEl) {
      stagesEl.innerHTML = `<div class="st-note" data-testid="vz-empty-reason">${esc(reasonFn(trace, viewCtx))}</div>`;
    }
  } else if (stagesEl) {
    stagesEl.innerHTML = "";
    _timelineHandle = mountStageTimeline(stagesEl, stages);
  }

  if (traceView) {
    traceView.setAttribute("data-testid", "pipeline-trace-view");
  }

  const io = resolvePipelineInputOutput(sources);
  renderIO(ioEl, io);

  const breakdown = resolveLatencyBreakdown(sources);
  renderLatency(latEl, breakdown);

  if (metaEl) {
    const bits = [];
    const action = trace.action || sources.pipelineTrace?.final_action;
    if (action) {
      bits.push(`verdict: <b class="action-${esc(String(action).toLowerCase())}">${esc(action)}</b>`);
    }
    if (blocked) {
      const why = blockMessage || trace.guard_reason || trace.detail
        || (trace.threat_type ? String(trace.threat_type).replace(/_/g, " ") : "");
      if (why) bits.push(`reason: <span data-testid="vz-block-why">${esc(why)}</span>`);
      else if (trace.threat_type) {
        bits.push(`threat: <span data-testid="vz-threat">${esc(String(trace.threat_type).replace(/_/g, " "))}</span>`);
      }
    } else if (trace.threat_type && !["none", "clean"].includes(String(trace.threat_type).toLowerCase())) {
      bits.push(`threat: <span data-testid="vz-threat">${esc(String(trace.threat_type).replace(/_/g, " "))}</span>`);
    }
    if (trace.request_id) {
      bits.push(`incident: <code data-testid="vz-incident">${esc(trace.request_id)}</code>`);
    }
    const total = resolveTotalLatencyMs(sources);
    if (total != null) {
      bits.push(`${Number(total).toFixed(1)}ms`);
    } else if (trace.processing_time_ms != null) {
      bits.push(`${Number(trace.processing_time_ms).toFixed(1)}ms`);
    }
    metaEl.innerHTML = bits.join(" · ");
  }
}
