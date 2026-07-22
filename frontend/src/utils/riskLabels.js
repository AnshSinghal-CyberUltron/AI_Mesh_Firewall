export const RISK_LABEL_TYPES = {
  risk: "Risk",
  behavioral: "Behavioral Risk",
  exposure: "Exposure Risk",
  severity: "Severity",
};

const BAND_TITLE = {
  low: "Low",
  medium: "Medium",
  high: "High",
  critical: "Critical",
};

export function formatRiskBandLabel(type, band) {
  const prefix = RISK_LABEL_TYPES[type] || "Risk";
  const key = String(band || "").toLowerCase();
  const title = BAND_TITLE[key] || (key ? key.charAt(0).toUpperCase() + key.slice(1) : "Unknown");
  return `${prefix}: ${title}`;
}

export function riskBandColorClass(band) {
  const key = String(band || "").toLowerCase();
  if (key === "critical" || key === "high") {
    return "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300";
  }
  if (key === "medium") {
    return "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300";
  }
  return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300";
}
