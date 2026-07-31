import { Component, lazy, Suspense, useCallback } from "react";
import { Routes, Route, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { DashboardLayout } from "./components/layout/DashboardLayout";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { RouteFallback } from "./components/module2/RouteFallback";
import { Module2ErrorState } from "./components/module2/PageStates";
import { resolveActiveTab, routeForTab } from "./utils/resolveActiveTab";
import { Login } from "./pages/Login";
import { OAuthCallback } from "./pages/OAuthCallback";
import { FirewallHome } from "./pages/FirewallHome";

/**
 * React.lazy caches a rejected import forever — a transient Vite miss leaves
 * Suspense stuck on "Loading module…". Retry inside the factory so one page
 * load can recover once the file is back.
 * Keep Module 2 pages lazy (not eager) so a bad page import cannot white-screen
 * the entire app at boot.
 * Never rethrow after retries: an uncaught lazy rejection unmounts the whole
 * React tree (blank body gradient, no sidebar). Surface an in-page error instead.
 */
function LazyImportError({ error }) {
  const message =
    error?.message ||
    "This module failed to load. Hard-refresh (Ctrl+Shift+R), or retry below.";
  return (
    <Module2ErrorState
      message={message}
      onRetry={() => {
        window.location.reload();
      }}
    />
  );
}

function lazyPage(importer) {
  return lazy(async () => {
    let lastErr;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        if (attempt > 0) await new Promise((r) => setTimeout(r, 300 * attempt));
        return await importer();
      } catch (err) {
        lastErr = err;
      }
    }
    console.error("[lazyPage] module import failed after retries", lastErr);
    return {
      default: function FailedLazyPage() {
        return <LazyImportError error={lastErr} />;
      },
    };
  });
}

/** Catches render/import errors inside the main pane without blanking the shell. */
class AppRouteErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, errorMessage: "" };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, errorMessage: error?.message || String(error) };
  }

  componentDidCatch(error, info) {
    console.error("[AppRouteErrorBoundary]", error, info?.componentStack);
  }

  render() {
    if (this.state.hasError) {
      return (
        <Module2ErrorState
          message={
            this.state.errorMessage
              ? `This view failed to render: ${this.state.errorMessage}`
              : "This view failed to render."
          }
          onRetry={() => {
            this.setState({ hasError: false, errorMessage: "" });
            window.location.reload();
          }}
        />
      );
    }
    return this.props.children;
  }
}

const DashboardPage = lazyPage(() =>
  import("./pages/module2/DashboardPage").then((m) => ({ default: m.DashboardPage }))
);
const UebaApiKeysPage = lazyPage(() =>
  import("./pages/module2/UebaApiKeysPage").then((m) => ({ default: m.UebaApiKeysPage }))
);
const ModelExposurePage = lazyPage(() =>
  import("./pages/module2/ModelAnalyticsPage").then((m) => ({ default: m.ModelExposurePage }))
);
const ThreatIntelPage = lazyPage(() =>
  import("./pages/module2/ThreatIntelPage").then((m) => ({ default: m.ThreatIntelPage }))
);
const IncidentsPage = lazyPage(() =>
  import("./pages/module2/IncidentsPage").then((m) => ({ default: m.IncidentsPage }))
);
const IncidentDetailPage = lazyPage(() =>
  import("./pages/module2/IncidentDetailPage").then((m) => ({ default: m.IncidentDetailPage }))
);
const McpRiskPage = lazyPage(() =>
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
    <AppRouteErrorBoundary>
      <DashboardLayout activeTab={activeTab} onTabChange={handleTabChange}>
        <Suspense fallback={<RouteFallback />}>
          <Routes>
            <Route path="/" element={<FirewallHome onTabChange={handleTabChange} />} />
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/ueba/api-keys" element={<UebaApiKeysPage />} />
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
    </AppRouteErrorBoundary>
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
