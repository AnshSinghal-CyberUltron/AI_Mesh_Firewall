/**
 * Vanilla port of frontend/src/components/simulator/StageTimeline.jsx
 * Hover + click-to-pin detail popover with full StageDetailCard fields.
 */
import {
  honestStageAction,
  normalizeStages,
  formatRouteDestination,
} from "./pipelineTrace.js";
import { summarizeRoutingDecision, routingHasTechnicalDetails } from "./routingExplain.js";
import { summarizeInputScanStage, inputScanHasTechnicalDetails } from "./inputScanExplain.js";
import {
  formatDecisionSource,
  formatDetectionTier,
  formatRoutingReason,
  formatZeroshieldScanSummary,
  ZEROSHIELD_GUARD_MODEL_LABEL,
} from "./zeroshieldBrand.js";

const SVG = {
  check: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg>',
  x: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>',
  alert: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/></svg>',
  shield: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>',
  clock: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>',
  reroute: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 3l4 4-4 4M3 7h18M7 21l-4-4 4-4M21 17H3"/></svg>',
  pin: '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 17v5M9 3h6l-1 7h3l-5 8-5-8h3L9 3z"/></svg>',
  close: '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>',
};

const ACTION_ICONS = {
  allow: SVG.check,
  block: SVG.x,
  flag: SVG.alert,
  redact: SVG.shield,
  skip: SVG.clock,
  needs_model: SVG.alert,
  reroute: SVG.reroute,
  error: SVG.alert,
};

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]
  ));
}

function formatStageLatency(stage) {
  const ms = Number(stage?.latency_ms);
  if (Number.isFinite(ms) && ms >= 0) {
    return `${ms < 1 ? "<1" : Math.round(ms * 10) / 10}ms latency`;
  }
  return "—";
}

const BEFORE_AFTER_LABELS = {
  policy: { before: "Before", after: "After policy redaction" },
  input_scan: { before: "Scanned input", after: "Forwarded to model" },
  model_input: { before: "Scanned input", after: "Forwarded to model" },
  output_guardrail: { before: "Model output", after: "After output guard" },
  default: { before: "Input", after: "Output" },
};

function technicalDetailsHtml(technical, kind) {
  if (kind === "routing" && !routingHasTechnicalDetails(technical)) return "";
  if (kind === "input_scan" && !inputScanHasTechnicalDetails(technical)) return "";

  const rows = [];
  if (kind === "routing") {
    if (technical.routing_reason) {
      rows.push(`<div><span class="pst-muted">Routing reason: </span><span>${esc(technical.routing_reason)}</span></div>`);
    }
    if (technical.policy_summary) {
      rows.push(`<div><span class="pst-muted">Policy: </span><span>${esc(technical.policy_summary)}</span></div>`);
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
      rows.push(`<div><span class="pst-muted">Weights: </span><span>${esc(w)}</span></div>`);
    }
    if (Number(technical.routing_score) > 0) {
      rows.push(`<div><span class="pst-muted">Score: </span><span>${Number(technical.routing_score).toFixed(3)}</span></div>`);
    }
    if (Number(technical.candidate_count) > 0) {
      rows.push(`<div><span class="pst-muted">Candidates: </span><span>${esc(technical.candidate_count)}</span></div>`);
    }
    if (Array.isArray(technical.fallback_chain) && technical.fallback_chain.length) {
      rows.push(`<div><span class="pst-muted">Fallback chain: </span><span class="pst-mono">${esc(technical.fallback_chain.join(" → "))}</span></div>`);
    }
  } else {
    if (technical.detail) {
      rows.push(`<div><span class="pst-muted">Detail: </span><span>${esc(technical.detail)}</span></div>`);
    }
    if (technical.tier) {
      rows.push(`<div><span class="pst-muted">Tier: </span><span>${esc(technical.tier)}</span></div>`);
    }
    if (technical.threat_type) {
      rows.push(`<div><span class="pst-muted">Threat: </span><span>${esc(String(technical.threat_type).replace(/_/g, " "))}</span></div>`);
    }
    if (technical.recommended_action) {
      rows.push(`<div><span class="pst-muted">Model recommendation: </span><span class="pst-upper">${esc(technical.recommended_action)}</span></div>`);
    }
    if (Array.isArray(technical.matched_patterns) && technical.matched_patterns.length) {
      rows.push(`<div><span class="pst-muted">Matched patterns: </span><span class="pst-mono">${esc(technical.matched_patterns.join(", "))}</span></div>`);
    }
    if (Array.isArray(technical.guard_findings) && technical.guard_findings.length) {
      rows.push(`<div><span class="pst-muted">Findings: </span><span>${esc(technical.guard_findings.join("; "))}</span></div>`);
    }
  }
  if (!rows.length) return "";
  return `<details class="pst-tech">
    <summary><span class="open-hide">Show technical details</span><span class="open-show">Hide technical details</span></summary>
    <div class="pst-tech-body">${rows.join("")}</div>
  </details>`;
}

function buildDetailHtml(stage, isPinned) {
  const displayAction = honestStageAction(stage);
  const hasWeights = stage.weights && typeof stage.weights === "object" && Object.keys(stage.weights).length > 0;
  const hasDecisionFactors = Array.isArray(stage.decision_factors) && stage.decision_factors.length > 0;
  const promptIn = typeof stage.prompt_in === "string" ? stage.prompt_in : "";
  const promptOut = typeof stage.prompt_out === "string" ? stage.prompt_out : "";
  const hasBeforeAfter = promptIn.length > 0 && promptOut.length > 0 && promptIn !== promptOut;
  const baLabels = BEFORE_AFTER_LABELS[stage.name] || BEFORE_AFTER_LABELS.default;

  const guardReason = typeof stage.guard_reason === "string" ? stage.guard_reason : "";
  const guardReasonLc = guardReason.toLowerCase();
  const inGuardReason = (value) => {
    const v = String(value ?? "").trim().toLowerCase();
    return v.length > 0 && guardReasonLc.includes(v);
  };
  const detailText = typeof stage.detail === "string" ? stage.detail.trim() : stage.detail;
  const showDetail = Boolean(detailText) && !(guardReason && inGuardReason(detailText));
  const dedupedPatterns = Array.isArray(stage.matched_patterns)
    ? stage.matched_patterns.filter((p) => !inGuardReason(p))
    : [];
  const dedupedFindings = Array.isArray(stage.guard_findings)
    ? stage.guard_findings.filter((f) => !inGuardReason(f))
    : [];
  const showPromptSubmitted = Boolean(stage.prompt_submitted) && !hasBeforeAfter;

  const isModelRouting = stage.name === "model_routing";
  const routingExplain = isModelRouting ? summarizeRoutingDecision(stage) : null;
  const isInputScan = stage.name === "input_scan";
  const inputScanExplain = isInputScan ? summarizeInputScanStage(stage) : null;
  const collapseInputScanGuardRaw = isInputScan && (
    stage.scan_outcome === "analyzed" || stage.redact_noop === true
  );

  const parts = [];
  parts.push(`<div class="pst-detail-head">
    <h4>${esc((stage.name || "").replace(/_/g, " "))}
      <span class="pst-badge action-${esc(displayAction)}">${esc(displayAction)}</span>
    </h4>
    <button type="button" class="pst-close" data-pst-close aria-label="${isPinned ? "Close" : "Pin"}">
      ${isPinned ? SVG.close : SVG.pin}<span>${isPinned ? "Close" : "Pin"}</span>
    </button>
  </div>`);

  parts.push(`<div class="pst-detail-grid">`);

  if (isModelRouting && routingExplain?.summary) {
    parts.push(`<div class="pst-why col-2">
      <div class="pst-why-label">Why this model</div>
      <p>${esc(routingExplain.summary)}</p>
      ${technicalDetailsHtml(routingExplain.technical, "routing")}
    </div>`);
  }
  if (isInputScan && inputScanExplain?.summary) {
    parts.push(`<div class="pst-why col-2">
      <div class="pst-why-label">Why this stage</div>
      <p>${esc(inputScanExplain.summary)}</p>
      ${technicalDetailsHtml(inputScanExplain.technical, "input_scan")}
    </div>`);
  }
  if (((stage.name === "output_guardrail" || stage.name === "policy")
    || (stage.name === "input_scan" && !collapseInputScanGuardRaw))
    && stage.guard_reason) {
    const title = stage.name === "policy"
      ? "Policy decision"
      : (stage.guard_model || ZEROSHIELD_GUARD_MODEL_LABEL);
    parts.push(`<div class="pst-why col-2">
      <div class="pst-why-label">${esc(title)}</div>
      <pre class="pst-pre">${esc(stage.guard_reason)}</pre>
      ${stage.reason_code ? `<div class="pst-reason-code">Reason code: <span class="pst-mono">${esc(stage.reason_code)}</span>${
        stage.recommended_action
          ? ` · Model recommendation: <span class="pst-upper">${esc(stage.recommended_action)}</span>`
          : ""
      }</div>` : ""}
    </div>`);
  }
  if (hasBeforeAfter) {
    parts.push(`<div class="col-2 pst-ba">
      <div><span class="pst-muted">${esc(baLabels.before)}:</span>
        <pre class="pst-pre block">${esc(promptIn)}</pre></div>
      <div><span class="pst-muted">${esc(baLabels.after)}:</span>
        <pre class="pst-pre block after">${esc(promptOut)}</pre></div>
    </div>`);
  }
  if (showDetail) {
    const label = stage.action === "block" ? "Block reason:" : stage.action === "error" ? "Failure reason:" : "Detail:";
    parts.push(`<div class="col-2"><span class="pst-muted">${label}</span> ${esc(detailText)}</div>`);
  }
  if (displayAction === "allow" && stage.name === "input_scan" && stage.tier) {
    const msg = stage.scan_outcome === "analyzed"
      ? `Scan ran (${stage.tier}) — context analyzed after policy redaction; no additional masking.`
      : `Scan ran (${stage.tier}) — request was not blocked; later stages executed normally.`;
    parts.push(`<div class="col-2 pst-ok-note">${esc(msg)}</div>`);
  }
  if (stage.name === "input_scan") {
    const scan = formatZeroshieldScanSummary({
      detection_tier: stage.tier,
      threat_type: stage.threat_type,
      confidence: stage.confidence,
      risk_score: stage.risk_score,
      scan_outcome: stage.scan_outcome,
      action: displayAction,
    });
    parts.push(`<div><span class="pst-muted">Threat:</span> <span class="${scan.clean ? "pst-ok" : "pst-bad"}">${esc(scan.threatLabel)}</span></div>`);
    parts.push(`<div><span class="pst-muted">${esc(scan.scoreLabel)}:</span> ${esc(scan.scoreValue)}</div>`);
  } else if (stage.threat_type && !["none", "clean"].includes(String(stage.threat_type).toLowerCase())) {
    parts.push(`<div><span class="pst-muted">Threat:</span> <span class="pst-bad">${esc(stage.threat_type)}</span></div>`);
  }
  parts.push(`<div><span class="pst-muted">Latency:</span> ${esc(formatStageLatency(stage).replace(" latency", ""))}</div>`);
  if (showPromptSubmitted) {
    parts.push(`<div class="col-2"><span class="pst-muted">Prompt submitted:</span>
      <pre class="pst-pre block">${esc(stage.prompt_submitted)}</pre></div>`);
  }
  if (stage.requested_model) {
    parts.push(`<div><span class="pst-muted">Requested model:</span> <span class="pst-mono">${esc(stage.requested_model)}</span></div>`);
  }
  if (stage.route_destination || stage.route_destination_label) {
    parts.push(`<div><span class="pst-muted">Destination:</span> <span class="pst-dest">${esc(stage.route_destination_label || formatRouteDestination(stage.route_destination))}</span></div>`);
  }
  if (stage.selected_model) {
    parts.push(`<div><span class="pst-muted">Selected model:</span> <span class="pst-mono">${esc(stage.selected_model)}</span></div>`);
  }
  if (stage.decision_source && !isModelRouting) {
    parts.push(`<div><span class="pst-muted">Decision source:</span> ${esc(stage.decision_source_label || formatDecisionSource(stage.decision_source))}</div>`);
  }
  if (stage.routing_reason && !isModelRouting) {
    parts.push(`<div class="col-2"><span class="pst-muted">Routing reason:</span> ${esc(formatRoutingReason(stage.routing_reason, { decisionSource: stage.decision_source }))}</div>`);
  }
  if (stage.policy_summary && !isModelRouting) {
    parts.push(`<div class="col-2"><span class="pst-muted">Policy summary:</span> ${esc(stage.policy_summary)}</div>`);
  }
  if (hasDecisionFactors && !isModelRouting) {
    parts.push(`<div class="col-2"><span class="pst-muted">Decision factors:</span> ${esc(stage.decision_factors.join(", "))}</div>`);
  }
  if (hasWeights && !isModelRouting) {
    const w = Object.entries(stage.weights).map(([k, v]) => {
      const raw = Number(v);
      return `${k}=${Number.isFinite(raw) ? `${Math.round(raw * 100)}%` : String(v)}`;
    }).join(", ");
    parts.push(`<div class="col-2"><span class="pst-muted">Applied weights:</span> ${esc(w)}</div>`);
  }
  if (Number(stage.routing_score) > 0 && !isModelRouting) {
    parts.push(`<div><span class="pst-muted">Routing score:</span> ${Number(stage.routing_score).toFixed(3)}</div>`);
  }
  if (Number(stage.candidate_count) > 0 && !isModelRouting) {
    parts.push(`<div><span class="pst-muted">Candidates:</span> ${esc(stage.candidate_count)}</div>`);
  }
  if (stage.tier && stage.name !== "input_scan") {
    parts.push(`<div><span class="pst-muted">Tier:</span> ${esc(formatDetectionTier(stage.tier))}</div>`);
  }
  if (Number(stage.confidence) > 0 && stage.name !== "input_scan") {
    parts.push(`<div><span class="pst-muted">Confidence:</span> ${Math.round(Number(stage.confidence) * 100)}%</div>`);
  }
  if (dedupedPatterns.length) {
    parts.push(`<div class="col-2"><span class="pst-muted">Patterns:</span> <span class="pst-warn">${esc(dedupedPatterns.join(", "))}</span></div>`);
  }
  if (dedupedFindings.length) {
    parts.push(`<div class="col-2"><span class="pst-muted">Evidence:</span> <span class="pst-warn">${esc(dedupedFindings.join(", "))}</span></div>`);
  }
  if (stage.matched_policies?.length) {
    parts.push(`<div class="col-2"><span class="pst-muted">Policies:</span> <span class="pst-sky">${esc(stage.matched_policies.join(", "))}</span></div>`);
  }
  if (stage.matched_rules?.length) {
    const label = ["block", "redact", "rewrite"].includes(stage.action) ? "Rules applied:" : "Rules matched:";
    parts.push(`<div class="col-2"><span class="pst-muted">${label}</span> <span class="pst-sky">${esc(stage.matched_rules.join(", "))}</span></div>`);
  }
  if (stage.docs_in !== undefined) {
    parts.push(`<div class="col-2"><span class="pst-muted">Docs:</span> ${esc(stage.docs_in)} in → ${esc(stage.docs_out ?? "—")} out${
      stage.docs_dropped != null ? ` (${esc(stage.docs_dropped)} dropped)` : ""
    }</div>`);
  }
  if (stage.content && !hasBeforeAfter) {
    parts.push(`<div class="col-2"><span class="pst-muted">Content:</span>
      <pre class="pst-pre block">${esc(stage.content)}</pre></div>`);
  }

  parts.push(`</div>`);
  return `<div class="pst-detail-card action-${esc(displayAction)}">${parts.join("")}</div>`;
}

/**
 * Mount StageTimeline into containerEl. Returns { destroy }.
 */
export function mountStageTimeline(containerEl, rawStages = []) {
  const stages = normalizeStages(rawStages);
  if (!containerEl) return { destroy() {} };
  containerEl.innerHTML = "";
  if (!stages.length) return { destroy() {} };

  let hoveredStage = null;
  let expandedStage = null;
  let popoverPos = { left: 180, top: 160, placement: "below" };
  const stageRefs = {};

  const root = document.createElement("div");
  root.className = "pst-timeline";
  root.setAttribute("data-testid", "pipeline-stage-timeline");

  const rail = document.createElement("div");
  rail.className = "pst-rail";
  root.appendChild(rail);

  stages.forEach((stage, i) => {
    const displayAction = honestStageAction(stage);
    const wrap = document.createElement("div");
    wrap.className = "pst-stage-wrap";

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `pst-stage stage action-${displayAction}`;
    btn.dataset.stage = stage.name || "";
    btn.dataset.action = displayAction;
    btn.setAttribute("aria-expanded", "false");
    btn.setAttribute(
      "aria-label",
      `Pipeline stage ${(stage.name || "").replace(/_/g, " ")}: ${displayAction}, ${formatStageLatency(stage)}.${
        displayAction === "block" && (stage.detail || stage.guard_reason)
          ? ` ${String(stage.detail || stage.guard_reason).replace(/\s+/g, " ").slice(0, 120)}`
          : ""
      }`,
    );
    // Card face stays compact (parity with StageTimeline.jsx): icon / badge /
    // name / latency / "View details". Block WHY lives only in the hover popover
    // (buildDetailHtml) — inline pst-stage-why stretched the Policy card.
    btn.innerHTML = `
      <div class="pst-stage-top">
        <span class="pst-icon action-${esc(displayAction)}">${ACTION_ICONS[displayAction] || SVG.clock}</span>
        <span class="pst-badge action-${esc(displayAction)}">${esc(displayAction)}</span>
      </div>
      <div class="pst-stage-name">${esc((stage.name || "").replace(/_/g, " "))}</div>
      <div class="pst-stage-lat">${esc(formatStageLatency(stage))}</div>
      <div class="pst-stage-hint"><span class="pst-dot action-${esc(displayAction)}"></span><span class="pst-hint-text">View details</span></div>
    `;
    stageRefs[i] = btn;

    btn.addEventListener("mouseenter", () => {
      hoveredStage = i;
      updatePopover();
    });
    btn.addEventListener("focus", () => {
      hoveredStage = i;
      updatePopover();
    });
    btn.addEventListener("blur", () => {
      if (hoveredStage === i) hoveredStage = null;
      updatePopover();
    });
    btn.addEventListener("click", () => {
      expandedStage = expandedStage === i ? null : i;
      updatePopover();
    });

    wrap.appendChild(btn);
    if (i < stages.length - 1) {
      const conn = document.createElement("div");
      conn.className = "pst-connector";
      wrap.appendChild(conn);
    }
    rail.appendChild(wrap);
  });

  const popover = document.createElement("div");
  popover.className = "pst-popover";
  popover.hidden = true;
  root.appendChild(popover);

  const tip = document.createElement("div");
  tip.className = "pst-tip";
  tip.textContent = "Tip: Hover or tap a stage for instant detail; click to pin while comparing stages.";
  root.appendChild(tip);

  root.addEventListener("mouseleave", () => {
    hoveredStage = null;
    updatePopover();
  });

  popover.addEventListener("mouseenter", () => {
    const idx = hoveredStage !== null ? hoveredStage : expandedStage;
    if (idx !== null) hoveredStage = idx;
  });
  popover.addEventListener("mouseleave", () => {
    hoveredStage = null;
    updatePopover();
  });

  function updatePopoverPosition(index) {
    const stageEl = stageRefs[index];
    if (!stageEl) return;
    const containerRect = root.getBoundingClientRect();
    const stageRect = stageEl.getBoundingClientRect();
    const desiredLeft = stageRect.left - containerRect.left + stageRect.width / 2;
    const halfPopover = Math.min(180, Math.max(96, containerRect.width / 2 - 8));
    const clampedLeft = Math.max(halfPopover, Math.min(desiredLeft, containerRect.width - halfPopover));
    const viewportH = window.innerHeight || 900;
    const POPOVER_EST = 360;
    const spaceBelow = viewportH - stageRect.bottom;
    const placeAbove = spaceBelow < POPOVER_EST && stageRect.top > spaceBelow;
    popoverPos = {
      left: clampedLeft,
      top: placeAbove
        ? stageRect.top - containerRect.top - 16
        : stageRect.bottom - containerRect.top + 16,
      placement: placeAbove ? "above" : "below",
    };
  }

  function updatePopover() {
    const visible = hoveredStage !== null ? hoveredStage : expandedStage;
    Object.keys(stageRefs).forEach((k) => {
      const i = Number(k);
      const el = stageRefs[i];
      const active = i === hoveredStage || i === expandedStage;
      el.classList.toggle("is-active", active);
      el.setAttribute("aria-expanded", i === expandedStage ? "true" : "false");
      const hint = el.querySelector(".pst-hint-text");
      if (hint) hint.textContent = i === expandedStage ? "Pinned" : "View details";
    });

    if (visible === null || !stages[visible]) {
      popover.hidden = true;
      popover.innerHTML = "";
      return;
    }
    updatePopoverPosition(visible);
    const isPinned = expandedStage === visible;
    popover.hidden = false;
    popover.className = `pst-popover placement-${popoverPos.placement}`;
    popover.style.left = `${popoverPos.left}px`;
    popover.style.top = `${popoverPos.top}px`;
    popover.innerHTML = buildDetailHtml(stages[visible], isPinned);
    const closeBtn = popover.querySelector("[data-pst-close]");
    if (closeBtn) {
      closeBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        expandedStage = null;
        hoveredStage = null;
        updatePopover();
      });
    }
  }

  containerEl.appendChild(root);
  return {
    destroy() {
      containerEl.innerHTML = "";
    },
  };
}
