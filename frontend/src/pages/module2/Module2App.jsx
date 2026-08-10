import { Outlet, useLocation } from "react-router-dom";
import { DashboardLayout } from "../../components/layout/DashboardLayout";

const ROUTE_TO_TAB = {
  "/dashboard": "m2-dashboard",
  "/ueba/api-keys": "m2-ueba-api-keys",
  "/threat-intel": "m2-threat-intel",
  "/threats/intelligence": "m2-threat-intel",
  "/models/exposure": "m2-models-exposure",
  "/mcp/risk": "m2-mcp-risk",
  "/incidents": "m2-incidents",
};

function resolveTab(pathname) {
  if (pathname.startsWith("/incidents/")) return "m2-incidents";
  return ROUTE_TO_TAB[pathname] || "m2-dashboard";
}

export function Module2Layout() {
  const location = useLocation();
  const activeTab = resolveTab(location.pathname);
  return (
    <DashboardLayout activeTab={activeTab}>
      <Outlet />
    </DashboardLayout>
  );
}

export {
  DashboardPage,
  UebaApiKeysPage,
  ModelExposurePage,
  IncidentsPage,
  IncidentDetailPage,
  ThreatIntelPage,
} from "./index";
