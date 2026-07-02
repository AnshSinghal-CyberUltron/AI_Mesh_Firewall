// Cumulative-sum raw uPlot data [x, y1, y2, …] into stacked plot data + bands,
// so filled areas visually stack (series 1 = bottom). Kept pure + separate from
// UPlotChart so the stacking math is unit-tested (data-integrity: a wrong stack
// silently misrepresents telemetry).
export function stackData(data) {
  const x = (data && data[0]) || [];
  const len = x.length;
  const accum = new Array(len).fill(0);
  const out = [x];
  for (let s = 1; s < data.length; s++) {
    const col = new Array(len);
    const src = data[s] || [];
    for (let i = 0; i < len; i++) {
      accum[i] += Number(src[i]) || 0;
      col[i] = accum[i];
    }
    out.push(col);
  }
  // Each series s (≥2) fills down to s-1; series 1 fills to baseline itself.
  const bands = [];
  for (let s = 2; s < data.length; s++) bands.push({ series: [s, s - 1] });
  return { data: out, bands };
}
