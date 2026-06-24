export function graduationPctComplete(progress) {
  if (!progress) return 0;
  return Math.min(100, Number(progress.pct_complete) || 0);
}

export function displayUebaScore(behavior) {
  return behavior?.final_score ?? behavior?.risk_score ?? 0;
}
