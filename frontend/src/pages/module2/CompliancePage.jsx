import { useCallback, useEffect, useState } from "react";
import { Loader2, Download } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { createModule2Api } from "../../api/module2";
import { PageHeader } from "../../components/module2/PageHeader";
import { ChartCard } from "../../components/module2/ChartCard";
import { PeriodSelector } from "../../components/module2/PeriodSelector";

export function CompliancePage() {
  const { fetchWithAuth } = useAuth();
  const api = createModule2Api(fetchWithAuth);
  const [period, setPeriod] = useState("30d");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await api.getCompliance(period));
    } finally {
      setLoading(false);
    }
  }, [period]);

  useEffect(() => { load(); }, [load]);

  const handleExport = async (format) => {
    const res = await api.exportCompliance(format, period);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `compliance-${period}.${format === "csv" ? "csv" : "html"}`;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (loading) return <div className="flex justify-center py-20"><Loader2 className="h-8 w-8 animate-spin text-teal-500" /></div>;

  const coverage = data?.owasp_coverage || {};

  return (
    <div>
      <PageHeader
        title="Compliance Center"
        subtitle="OWASP AI Top 10 coverage and framework violations"
        actions={
          <>
            <PeriodSelector value={period} onChange={setPeriod} />
            <button onClick={() => handleExport("csv")} className="flex items-center gap-1 rounded-lg border border-slate-200 px-3 py-1.5 text-sm dark:border-slate-600">
              <Download className="h-4 w-4" /> CSV
            </button>
          </>
        }
      />

      <div className="mb-6 grid grid-cols-3 gap-4">
        <div className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-800/60">
          <p className="text-xs text-slate-500">Overall Coverage</p>
          <p className="text-3xl font-bold text-teal-600">{data?.overall_coverage_pct ?? 0}%</p>
        </div>
        <div className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-800/60">
          <p className="text-xs text-slate-500">Vectors Covered</p>
          <p className="text-3xl font-bold">{data?.owasp_vectors_covered ?? 0}/{data?.owasp_vectors_total ?? 30}</p>
        </div>
        <div className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-800/60">
          <p className="text-xs text-slate-500">Violations</p>
          <p className="text-3xl font-bold text-red-600">{data?.total_violations ?? 0}</p>
        </div>
      </div>

      <ChartCard title="OWASP AI Top 10 Scorecard">
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {Object.entries(coverage).map(([code, stats]) => (
            <div key={code} className="rounded-lg border border-slate-100 p-3 dark:border-slate-700">
              <div className="flex justify-between">
                <span className="font-mono text-sm font-semibold">{code}</span>
                <span className={`text-sm ${stats.coverage_pct >= 80 ? "text-green-600" : stats.detected ? "text-amber-600" : "text-slate-400"}`}>
                  {stats.coverage_pct != null ? `${stats.coverage_pct}%` : "—"}
                </span>
              </div>
              <p className="mt-1 text-xs text-slate-400">{stats.detected} detected · {stats.blocked} blocked</p>
            </div>
          ))}
        </div>
      </ChartCard>
    </div>
  );
}
