import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, GraduationCap, RefreshCw } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { clearModule2Cache, createModule2Api } from "../../api/module2";
import { PageHeader } from "../../components/module2/PageHeader";
import { ContextualAppBar } from "../../components/module2/ContextualAppBar";
import { UebaGraduationSettings } from "../../components/module2/UebaGraduationSettings";
import { UebaLearningKeysTable } from "../../components/module2/UebaLearningKeysTable";
import { UebaModesGuide } from "../../components/module2/UebaModesGuide";
import {
  Module2ErrorState,
  Module2PageErrorBoundary,
  Module2PageSkeleton,
} from "../../components/module2/PageStates";
import { ANALYST_BRIEF_TITLE, PAGE_BRIEFS } from "./pageCopy";

export function UebaLearningSettingsPage() {
  return (
    <Module2PageErrorBoundary title="UEBA learning mode & settings failed to render">
      <UebaLearningSettingsPageInner />
    </Module2PageErrorBoundary>
  );
}

function UebaLearningSettingsPageInner() {
  const { fetchWithAuth, user } = useAuth();
  const api = useMemo(() => createModule2Api(fetchWithAuth), [fetchWithAuth]);
  const isAdmin = !!user?.is_superuser || !!user?.is_staff || (user?.roles || []).includes("platform_admin");
  const [learningKeys, setLearningKeys] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      clearModule2Cache();
      const learning = await api.getUebaLearningKeys({ useCache: false });
      setLearningKeys(learning);
    } catch (err) {
      setLoadError(err.message || "Failed to load learning mode keys.");
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    load();
  }, [load]);

  const rows = learningKeys?.results || [];
  const learningCount = rows.length;

  if (loading && !learningKeys) {
    return (
      <div>
        <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.uebaLearning} />
        {loadError ? <Module2ErrorState message={loadError} onRetry={load} /> : <Module2PageSkeleton />}
      </div>
    );
  }

  if (loadError && !learningKeys) {
    return (
      <div>
        <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.uebaLearning} />
        <Module2ErrorState message={loadError} onRetry={load} />
      </div>
    );
  }

  return (
    <div>
      <ContextualAppBar title={ANALYST_BRIEF_TITLE} description={PAGE_BRIEFS.uebaLearning} />

      <Link
        to="/ueba/api-keys"
        className="mb-4 inline-flex items-center gap-1 text-sm text-teal-600 hover:underline dark:text-teal-400"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to UEBA overview
      </Link>

      <PageHeader
        title="Learning Mode Keys & Settings"
        subtitle="Org graduation thresholds and keys still building behavioral baselines"
        actions={
          <button
            type="button"
            onClick={load}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2 text-sm disabled:opacity-50 dark:border-slate-600"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
        }
      />

      <UebaModesGuide defaultExpanded />

      <UebaGraduationSettings api={api} isAdmin={isAdmin} expanded onSaved={load} />

      <div className="rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-700 dark:bg-slate-800/60">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3 dark:border-slate-700">
          <div className="flex items-center gap-2">
            <GraduationCap className="h-4 w-4 text-teal-600 dark:text-teal-400" />
            <div>
              <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">Learning mode keys</h3>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Keys graduate when org request count <em>or</em> age threshold is met
              </p>
            </div>
          </div>
          <span className="rounded-full bg-teal-100 px-2.5 py-0.5 text-xs font-semibold text-teal-800 dark:bg-teal-900/40 dark:text-teal-200">
            {learningCount} key{learningCount === 1 ? "" : "s"}
          </span>
        </div>
        <UebaLearningKeysTable rows={rows} loading={loading} />
      </div>
    </div>
  );
}
