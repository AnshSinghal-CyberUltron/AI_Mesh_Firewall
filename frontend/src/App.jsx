import { lazy, Suspense, useCallback } from "react";
import { Routes, Route, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { DashboardLayout } from "./components/layout/DashboardLayout";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { RouteFallback } from "./components/module2/RouteFallback";
import { resolveActiveTab, routeForTab } from "./utils/resolveActiveTab";
import { Login } from "./pages/Login";
import { OAuthCallback } from "./pages/OAuthCallback";
import { FirewallHome } from "./pages/FirewallHome";

const DashboardPage = lazy(() =>
  import("./pages/module2/DashboardPage").then((m) => ({ default: m.DashboardPage }))
);
const UebaApiKeysPage = lazy(() =>
  import("./pages/module2/UebaApiKeysPage").then((m) => ({ default: m.UebaApiKeysPage }))
);
const UebaLearningSettingsPage = lazy(() =>
  import("./pages/module2/UebaLearningSettingsPage").then((m) => ({ default: m.UebaLearningSettingsPage }))
);
const ModelExposurePage = lazy(() =>
  import("./pages/module2/ModelAnalyticsPage").then((m) => ({ default: m.ModelExposurePage }))
);
const ThreatIntelPage = lazy(() =>
  import("./pages/module2/ThreatIntelPage").then((m) => ({ default: m.ThreatIntelPage }))
);
const IncidentsPage = lazy(() =>
  import("./pages/module2/IncidentsPage").then((m) => ({ default: m.IncidentsPage }))
);
const IncidentDetailPage = lazy(() =>
  import("./pages/module2/IncidentDetailPage").then((m) => ({ default: m.IncidentDetailPage }))
);
const McpRiskPage = lazy(() =>
  import("./pages/module2/McpRiskPage").then((m) => ({ default: m.McpRiskPage }))
);

function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const activeTab = resolveActiveTab(location.pathname, searchParams);

  const handleTabChange = useCallback(
    (tab) => {
      const module2Route = routeForTab(tab);
      if (module2Route) {
        navigate(module2Route);
        return;
      }
      if (tab === "firewall") {
        navigate("/");
        return;
      }
      if (tab === "profile" || tab === "settings" || tab.startsWith("firewall")) {
        navigate(tab === "firewall" ? "/" : `/?tab=${tab}`);
      }
    },
    [navigate]
  );

  return (
    <DashboardLayout activeTab={activeTab} onTabChange={handleTabChange}>
      <Suspense fallback={<RouteFallback />}>
        <Routes>
          <Route path="/" element={<FirewallHome onTabChange={handleTabChange} />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/ueba/api-keys" element={<UebaApiKeysPage />} />
          <Route path="/ueba/api-keys/learning" element={<UebaLearningSettingsPage />} />
          <Route path="/models/exposure" element={<ModelExposurePage />} />
          <Route path="/mcp/risk" element={<McpRiskPage />} />
          <Route path="/threat-intel" element={<ThreatIntelPage />} />
          <Route path="/threats/intelligence" element={<ThreatIntelPage />} />
          <Route path="/incidents" element={<IncidentsPage />} />
          <Route path="/incidents/:id" element={<IncidentDetailPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </DashboardLayout>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/signup" element={<Navigate to="/login" replace />} />
      <Route path="/oauth/callback" element={<OAuthCallback />} />
      <Route
        path="/*"
        element={
          <ProtectedRoute>
            <AppShell />
          </ProtectedRoute>
        }
      />
    </Routes>
  );
}
