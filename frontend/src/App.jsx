import { lazy, Suspense, useCallback, useEffect } from "react";
import {
  Routes,
  Route,
  Navigate,
  Outlet,
  useLocation,
  useNavigate,
  useOutletContext,
  useSearchParams,
} from "react-router-dom";
import { DashboardLayout } from "./components/layout/DashboardLayout";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { LazyRouteErrorBoundary } from "./components/LazyRouteErrorBoundary";
import { RouteFallback } from "./components/module2/RouteFallback";
import { resolveActiveTab, routeForTab } from "./utils/resolveActiveTab";
import { lazyImportWithTimeout } from "./utils/lazyImportWithTimeout";
import { Login } from "./pages/Login";
import { OAuthCallback } from "./pages/OAuthCallback";

// FirewallHome transitively imports every Module-1 panel + simulator (the heaviest part
// of the app). Keep it lazy so /login does not pay for it up front.
const loadFirewallHome = lazyImportWithTimeout(
  () => import("./pages/FirewallHome").then((m) => ({ default: m.FirewallHome })),
  { label: "FirewallHome", timeoutMs: 60000, retries: 1 },
);
const FirewallHome = lazy(loadFirewallHome);

const DashboardPage = lazy(
  lazyImportWithTimeout(
    () => import("./pages/module2/DashboardPage").then((m) => ({ default: m.DashboardPage })),
    { label: "DashboardPage" },
  ),
);
const UebaApiKeysPage = lazy(
  lazyImportWithTimeout(
    () => import("./pages/module2/UebaApiKeysPage").then((m) => ({ default: m.UebaApiKeysPage })),
    { label: "UebaApiKeysPage", timeoutMs: 60000, retries: 1 },
  ),
);
const ModelExposurePage = lazy(
  lazyImportWithTimeout(
    () => import("./pages/module2/ModelAnalyticsPage").then((m) => ({ default: m.ModelExposurePage })),
    { label: "ModelExposurePage" },
  ),
);
const ThreatIntelPage = lazy(
  lazyImportWithTimeout(
    () => import("./pages/module2/ThreatIntelPage").then((m) => ({ default: m.ThreatIntelPage })),
    { label: "ThreatIntelPage" },
  ),
);
const IncidentsPage = lazy(
  lazyImportWithTimeout(
    () => import("./pages/module2/IncidentsPage").then((m) => ({ default: m.IncidentsPage })),
    { label: "IncidentsPage" },
  ),
);
const IncidentDetailPage = lazy(
  lazyImportWithTimeout(
    () => import("./pages/module2/IncidentDetailPage").then((m) => ({ default: m.IncidentDetailPage })),
    { label: "IncidentDetailPage" },
  ),
);
const McpRiskPage = lazy(
  lazyImportWithTimeout(
    () => import("./pages/module2/McpRiskPage").then((m) => ({ default: m.McpRiskPage })),
    { label: "McpRiskPage" },
  ),
);

function FirewallHomeRoute() {
  const { onTabChange } = useOutletContext();
  return <FirewallHome onTabChange={onTabChange} />;
}

function isModule2Path(pathname) {
  return (
    pathname.startsWith("/ueba")
    || pathname.startsWith("/models")
    || pathname.startsWith("/mcp")
    || pathname.startsWith("/threat")
    || pathname.startsWith("/incidents")
    || pathname === "/dashboard"
  );
}

/**
 * Layout route (no path splat) so Module-1/2 child paths match the full URL.
 * The old pattern — parent `path="/*"` + nested `<Routes>` — broke Module 2 under
 * React Router 7 relative splat matching (URL changed, UI stayed on Module 1).
 */
function ProtectedShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const activeTab = resolveActiveTab(location.pathname, searchParams);

  // Prefetch FirewallHome only when already on Module 1 home. Prefetching the
  // heaviest chunk while Module 2 lazy routes compete for Vite transforms on
  // Docker Desktop Windows bind-mounts wedges FSWatcher (EIO) and leaves
  // Suspense stuck on "Loading module…".
  useEffect(() => {
    if (isModule2Path(location.pathname)) {
      return undefined;
    }
    const idle = window.requestIdleCallback
      ? window.requestIdleCallback(() => {
          loadFirewallHome().catch(() => {});
        })
      : setTimeout(() => {
          loadFirewallHome().catch(() => {});
        }, 300);
    return () => {
      if (window.cancelIdleCallback && typeof idle === "number") {
        window.cancelIdleCallback(idle);
      } else {
        clearTimeout(idle);
      }
    };
  }, [location.pathname]);

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
      <LazyRouteErrorBoundary>
        <Suspense fallback={<RouteFallback label="Loading module…" />}>
          <Outlet context={{ onTabChange: handleTabChange }} />
        </Suspense>
      </LazyRouteErrorBoundary>
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
        element={
          <ProtectedRoute>
            <ProtectedShell />
          </ProtectedRoute>
        }
      >
        <Route path="/" element={<FirewallHomeRoute />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/ueba/api-keys" element={<UebaApiKeysPage />} />
        <Route path="/models/exposure" element={<ModelExposurePage />} />
        <Route path="/mcp/risk" element={<McpRiskPage />} />
        <Route path="/threat-intel" element={<ThreatIntelPage />} />
        <Route path="/threats/intelligence" element={<ThreatIntelPage />} />
        <Route path="/incidents" element={<IncidentsPage />} />
        <Route path="/incidents/:id" element={<IncidentDetailPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
