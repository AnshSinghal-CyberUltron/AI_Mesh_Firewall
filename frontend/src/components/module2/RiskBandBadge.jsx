import { formatRiskBandLabel, riskBandColorClass } from "../../utils/riskLabels";

export function RiskBandBadge({ type, band, score, className = "" }) {
  if (!band) return null;
  const label = formatRiskBandLabel(type, band);
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-semibold ${riskBandColorClass(band)} ${className}`}
      title={score != null ? `Score ${score}` : label}
    >
      {label}
      {score != null && <span className="font-normal opacity-80">({score})</span>}
    </span>
  );
}
