import { BookOpen, X } from "lucide-react";

const DEFAULT_MEDIUM = 0.35;
const DEFAULT_HIGH = 0.7;

const RISK_BAND_ROWS = [
  {
    band: "Safe (Low)",
    range: "0% – 34%",
    scoreExample: "0.12",
    meaning: "Traffic looks normal for this key. Few blocks, no major spikes.",
    action: "Keep monitoring. No urgent SOC action.",
  },
  {
    band: "Elevated (Medium)",
    range: "35% – 69%",
    scoreExample: "0.41",
    meaning: "More blocks, velocity bursts, or policy signals than baseline. A score like 0.412 sits here — worth a look, not an emergency.",
    action: "Review recent prompts, models used, and threat types. Confirm the key owner and intended use.",
  },
  {
    band: "Risky (High)",
    range: "70% – 100%",
    scoreExample: "0.78",
    meaning: "Strong abuse or anomaly pattern — high block rate, severe threats, or LLM analyst concern.",
    action: "Prioritize investigation. Consider disable key or credential kill-switch.",
  },
];

const SCORE_TYPE_ROWS = [
  {
    name: "Behavioral risk score",
    what: "Single 0–1 score on the gauge and fleet table (shown as 0–100% on the dial).",
    notSameAs: "Not the same as block % or allowed % — it combines several signals with org weights.",
  },
  {
    name: "Safety rates (blocked / redacted / allowed)",
    what: "Simple percentages of requests in this time window.",
    notSameAs: "Allowed % is “safe bandwidth” (clean traffic). A key can have high allowed % but medium behavioral risk if velocity or threat severity is elevated.",
  },
  {
    name: "Traditional vs final score",
    what: "Traditional = weighted formula only. Final may add baseline deviation + LLM adjustment.",
    notSameAs: "The badge band uses the final score after reassessment completes.",
  },
  {
    name: "Recommended action (LLM)",
    what: "Monitor, Investigate, or Contain — analyst hint separate from the numeric band.",
    notSameAs: "A “Monitor” action can still show medium band if math says so.",
  },
];

const RECOMMENDED_ACTIONS = [
  { label: "Monitor", detail: "Watch the key; no immediate containment." },
  { label: "Investigate", detail: "Pull recent prompts, check models and threat types, validate owner intent." },
  { label: "Contain", detail: "Consider kill-switch or disable key — high-confidence abuse signal." },
];

function bandForScore(score, medium = DEFAULT_MEDIUM, high = DEFAULT_HIGH) {
  const n = Number(score);
  if (Number.isNaN(n)) return "—";
  if (n >= high) return "Risky (High)";
  if (n >= medium) return "Elevated (Medium)";
  return "Safe (Low)";
}

export function UebaScoreGuideModal({ open, onClose, mediumThreshold, highThreshold }) {
  if (!open) return null;

  const med = mediumThreshold ?? DEFAULT_MEDIUM;
  const high = highThreshold ?? DEFAULT_HIGH;
  const exampleScore = 0.412;
  const exampleBand = bandForScore(exampleScore, med, high);

  const bandRows = RISK_BAND_ROWS.map((row, i) => {
    if (i === 1) {
      return {
        ...row,
        range: `${Math.round(med * 100)}% – ${Math.round(high * 100) - 1}%`,
        scoreExample: String(exampleScore),
        meaning: `More blocks, velocity bursts, or policy signals than baseline. Your simulator key at ${exampleScore} is ${exampleBand} — review prompts, not an automatic block.`,
      };
    }
    if (i === 0) return { ...row, range: `0% – ${Math.round(med * 100) - 1}%` };
    if (i === 2) return { ...row, range: `${Math.round(high * 100)}% – 100%` };
    return row;
  });

  return (
    <>
      <button
        type="button"
        aria-label="Close score guide"
        className="fixed inset-0 z-[95] bg-black/45"
        onClick={onClose}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="ueba-score-guide-title"
        className="fixed left-1/2 top-1/2 z-[100] flex max-h-[min(88vh,720px)] w-[min(92vw,560px)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-900"
      >
        <header className="flex shrink-0 items-start justify-between gap-3 border-b border-slate-200 px-4 py-3 dark:border-slate-700">
          <div className="flex items-start gap-2">
            <BookOpen className="mt-0.5 h-5 w-5 shrink-0 text-teal-600 dark:text-teal-400" />
            <div>
              <h2 id="ueba-score-guide-title" className="text-sm font-semibold text-slate-900 dark:text-white">
                UEBA score guide
              </h2>
              <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                Plain-language reference for M2.2 behavioral risk
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          <div className="space-y-5">
            <section>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-teal-700 dark:text-teal-300">
                Risk bands — what score means
              </h3>
              <p className="mt-1.5 text-sm text-slate-600 dark:text-slate-300">
                Behavioral risk is a number from <strong>0 to 1</strong> (0%–100%). Your org thresholds:
                medium from <strong>{Math.round(med * 100)}%</strong>, high from <strong>{Math.round(high * 100)}%</strong>.
              </p>
              <div className="mt-3 overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
                <table className="w-full min-w-[480px] text-left text-xs">
                  <thead className="bg-slate-50 text-slate-500 dark:bg-slate-800/80 dark:text-slate-400">
                    <tr>
                      <th className="px-3 py-2 font-semibold">Band</th>
                      <th className="px-3 py-2 font-semibold">Score range</th>
                      <th className="px-3 py-2 font-semibold">What it means</th>
                      <th className="px-3 py-2 font-semibold">What to do</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {bandRows.map((row) => (
                      <tr key={row.band} className="text-slate-700 dark:text-slate-200">
                        <td className="px-3 py-2.5 font-semibold whitespace-nowrap">{row.band}</td>
                        <td className="px-3 py-2.5 font-mono whitespace-nowrap">{row.range}</td>
                        <td className="px-3 py-2.5 leading-relaxed">{row.meaning}</td>
                        <td className="px-3 py-2.5 leading-relaxed text-slate-600 dark:text-slate-300">{row.action}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 rounded-md border border-teal-200/80 bg-teal-50/50 px-3 py-2 text-xs text-teal-900 dark:border-teal-900/50 dark:bg-teal-950/30 dark:text-teal-100">
                <strong>Example:</strong> Simulator key score <strong className="font-mono">{exampleScore}</strong> ={" "}
                <strong>{exampleBand}</strong>. Check recent prompts and block rate — the number alone does not auto-block traffic.
              </p>
            </section>

            <section>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-teal-700 dark:text-teal-300">
                Behavioral risk vs other numbers
              </h3>
              <div className="mt-2 overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
                <table className="w-full min-w-[440px] text-left text-xs">
                  <thead className="bg-slate-50 text-slate-500 dark:bg-slate-800/80 dark:text-slate-400">
                    <tr>
                      <th className="px-3 py-2 font-semibold">Metric</th>
                      <th className="px-3 py-2 font-semibold">What it is</th>
                      <th className="px-3 py-2 font-semibold">Not the same as</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {SCORE_TYPE_ROWS.map((row) => (
                      <tr key={row.name} className="text-slate-700 dark:text-slate-200">
                        <td className="px-3 py-2.5 font-semibold">{row.name}</td>
                        <td className="px-3 py-2.5 leading-relaxed">{row.what}</td>
                        <td className="px-3 py-2.5 leading-relaxed text-slate-600 dark:text-slate-300">{row.notSameAs}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-teal-700 dark:text-teal-300">
                Safety rates (safe bandwidth)
              </h3>
              <p className="mt-1.5 text-sm leading-relaxed text-slate-600 dark:text-slate-300">
                <strong>Blocked</strong> — stopped by policy. <strong>Redacted</strong> — sensitive data scrubbed.
                <strong> Allowed (safe bandwidth)</strong> — requests that passed clean. These are simple ratios;
                they do not replace the behavioral risk score.
              </p>
            </section>

            <section>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-teal-700 dark:text-teal-300">
                Recommended actions
              </h3>
              <ul className="mt-2 space-y-2">
                {RECOMMENDED_ACTIONS.map((item) => (
                  <li
                    key={item.label}
                    className="rounded-md border border-slate-100 bg-slate-50 px-3 py-2 dark:border-slate-700 dark:bg-slate-800/50"
                  >
                    <p className="text-xs font-semibold text-slate-800 dark:text-slate-100">{item.label}</p>
                    <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{item.detail}</p>
                  </li>
                ))}
              </ul>
            </section>
          </div>
        </div>
      </div>
    </>
  );
}
