const PHASE_STYLES = {
  triage_eligible: "border-teal-300 bg-teal-50 text-teal-900 dark:border-teal-800 dark:bg-teal-950/40 dark:text-teal-100",
  profile_building: "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100",
  below_triage_threshold: "border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-900/40 dark:text-slate-200",
  monitoring: "border-sky-300 bg-sky-50 text-sky-900 dark:border-sky-800 dark:bg-sky-950/40 dark:text-sky-100",
  llm_disabled: "border-slate-200 bg-slate-100 text-slate-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300",
};

const PHASE_LABELS = {
  triage_eligible: "LLM triage eligible",
  profile_building: "Building LLM profile",
  below_triage_threshold: "Below LLM triage threshold",
  monitoring: "LLM monitoring",
  llm_disabled: "LLM triage disabled",
};

export function LlMObservationBadge({ observation, compact = false }) {
  if (!observation) return null;
  const phase = observation.observation_phase || "profile_building";
  const style = PHASE_STYLES[phase] || PHASE_STYLES.profile_building;
  const label = PHASE_LABELS[phase] || phase;

  if (compact) {
    return (
      <span
        className={`inline-flex rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${style}`}
        title={`Prompt target ${observation.prompt_target} req · triage ≥ ${observation.triage_threshold}`}
      >
        {observation.requests_meet_prompt_target ? "Observed" : `<${observation.prompt_target} req`}
      </span>
    );
  }

  return (
    <span className={`inline-flex rounded-md border px-2 py-0.5 text-[10px] font-semibold uppercase ${style}`}>
      {label}
    </span>
  );
}

export function LlMObservationPanel({ observation, traditionalScore }) {
  if (!observation) return null;
  const reqCount = observation.request_count ?? 0;
  const promptTarget = observation.prompt_target ?? 50;
  const triageThreshold = observation.triage_threshold ?? 0.45;
  const traditional = traditionalScore ?? 0;

  return (
    <div className="rounded-lg border border-slate-200 p-3 text-xs dark:border-slate-600">
      <p className="mb-2 font-semibold uppercase text-slate-500">LLM observation gates</p>
      <ul className="space-y-2 text-slate-700 dark:text-slate-300">
        <li className="flex items-start justify-between gap-2">
          <span>Requests vs prompt target ({promptTarget})</span>
          <span className={`font-mono font-semibold ${observation.requests_meet_prompt_target ? "text-teal-600" : "text-amber-600"}`}>
            {reqCount} {observation.requests_meet_prompt_target ? "≥ target" : "< target"}
          </span>
        </li>
        <li className="flex items-start justify-between gap-2">
          <span>Traditional score vs triage threshold ({triageThreshold})</span>
          <span className={`font-mono font-semibold ${observation.triage_score_gate_met ? "text-teal-600" : "text-slate-500"}`}>
            {Number(traditional).toFixed(3)} {observation.triage_score_gate_met ? "≥ threshold" : "< threshold"}
          </span>
        </li>
        <li className="flex items-start justify-between gap-2">
          <span>Behavior profile</span>
          <span className={observation.profile_ready ? "font-semibold text-teal-600" : "text-amber-600"}>
            {observation.profile_ready ? "Ready" : `Collecting (${observation.prompt_samples_collected ?? 0}/${promptTarget})`}
          </span>
        </li>
      </ul>
      <p className="mt-2 text-[11px] text-slate-500">
        {observation.llm_triage_eligible
          ? "This key meets org gates — LLM triage can run when reassessed."
          : observation.requests_below_prompt_target
            ? `Keys need ≥ ${promptTarget} requests (or collected prompts) before the LLM behavior profile completes.`
            : observation.profile_ready && !observation.triage_score_gate_met
              ? `Triage runs when traditional score ≥ ${triageThreshold} or anomaly flags fire.`
              : "LLM observation uses org score-calculation settings above."}
      </p>
    </div>
  );
}
