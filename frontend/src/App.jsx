import { useEffect, useRef, useState } from "react";
import { Routes, Route, Navigate, useSearchParams } from "react-router-dom";
import { useAuth } from "./context/AuthContext";
import { DashboardLayout } from "./components/layout/DashboardLayout";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { Login } from "./pages/Login";
import { Profile } from "./pages/Profile";
import { Settings } from "./pages/Settings";
import { OAuthCallback } from "./pages/OAuthCallback";
import { AIMeshFirewallOverview } from "./pages/AIMeshFirewallOverview";
import {
  Firewall11Page,
  Firewall12Page,
  Firewall13Page,
  Firewall14Page,
  Firewall15Page,
  Firewall16Page,
  Firewall17Page,
} from "./pages/firewall-submodules";
import { AIMeshFirewallConfig } from "./pages/AIMeshFirewallConfig";
import { SubModuleResultsPage } from "./components/SubmoduleResultsPage";
import { LogDetailPage } from "./components/LogDetailPage";

const firewallTabToModuleId = {
  "firewall-1-1": "1.1",
  "firewall-1-2": "1.2",
  "firewall-1-3": "1.3",
  "firewall-1-4": "1.4",
  "firewall-1-5": "1.5",
  "firewall-1-6": "1.6",
  "firewall-1-7": "1.7",
};

// Whitelist of valid ?tab= values. Anything else (typos, stale links,
// crafted URLs) falls back to the overview tab instead of leaking an
// arbitrary string into layout/navigation state.
const KNOWN_TABS = new Set([
  "firewall",
  "profile",
  "settings",
  "firewall-config",
  ...Object.keys(firewallTabToModuleId),
]);

function DashboardApp() {
  const { user } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const rawTab = searchParams.get("tab");
  const activeTab = rawTab && KNOWN_TABS.has(rawTab) ? rawTab : "firewall";
  const [resultsTab, setResultsTab] = useState(null);
  const [logDetailData, setLogDetailData] = useState(null);
  const tabInitializedRef = useRef(false);

  const setActiveTab = (tab) => {
    if (tab === "firewall") {
      setSearchParams({}, { replace: true });
    } else {
      setSearchParams({ tab }, { replace: true });
    }
  };

  useEffect(() => {
    if (!user || tabInitializedRef.current) return;
    const tab = searchParams.get("tab");
    // Normalize missing or unknown ?tab= values to the overview URL once on
    // first authenticated render.
    if (!tab || !KNOWN_TABS.has(tab)) {
      setSearchParams({}, { replace: true });
    }
    tabInitializedRef.current = true;
  }, [user, searchParams, setSearchParams]);

  const handleViewResults = (tabId) => {
    setResultsTab(tabId);
    setLogDetailData(null);
  };

  const handleViewLogDetail = (logData) => {
    setLogDetailData(logData);
  };

  const handleBackFromResults = () => {
    setResultsTab(null);
    setLogDetailData(null);
  };

  const handleBackFromLog = () => {
    setLogDetailData(null);
  };

  const handleTabChange = (tab) => {
    setActiveTab(tab);
    setResultsTab(null);
    setLogDetailData(null);
  };

  const renderContent = () => {
    if (logDetailData) {
      return <LogDetailPage logData={logDetailData} onBack={handleBackFromLog} />;
    }

    if (resultsTab && firewallTabToModuleId[resultsTab]) {
      return (
        <SubModuleResultsPage
          moduleId={firewallTabToModuleId[resultsTab]}
          onBack={handleBackFromResults}
          onViewLogDetail={handleViewLogDetail}
        />
      );
    }

    if (activeTab === "profile") return <Profile />;
    if (activeTab === "settings") return <Settings />;
    if (activeTab === "firewall") {
      return <AIMeshFirewallOverview onTabChange={handleTabChange} />;
    }
    if (activeTab === "firewall-1-1") {
      return (
        <Firewall11Page
          onViewResults={() => handleViewResults("firewall-1-1")}
          onViewLogDetail={handleViewLogDetail}
        />
      );
    }
    if (activeTab === "firewall-1-2") {
      return (
        <Firewall12Page
          onViewResults={() => handleViewResults("firewall-1-2")}
          onViewLogDetail={handleViewLogDetail}
        />
      );
    }
    if (activeTab === "firewall-1-3") {
      return (
        <Firewall13Page
          onViewResults={() => handleViewResults("firewall-1-3")}
          onViewLogDetail={handleViewLogDetail}
        />
      );
    }
    if (activeTab === "firewall-1-4") {
      return (
        <Firewall14Page
          onViewResults={() => handleViewResults("firewall-1-4")}
          onViewLogDetail={handleViewLogDetail}
        />
      );
    }
    if (activeTab === "firewall-1-5") {
      return (
        <Firewall15Page
          onViewResults={() => handleViewResults("firewall-1-5")}
          onViewLogDetail={handleViewLogDetail}
        />
      );
    }
    if (activeTab === "firewall-1-6") {
      return (
        <Firewall16Page
          onViewResults={() => handleViewResults("firewall-1-6")}
          onViewLogDetail={handleViewLogDetail}
        />
      );
    }
    if (activeTab === "firewall-1-7") {
      return (
        <Firewall17Page
          onViewResults={() => handleViewResults("firewall-1-7")}
          onViewLogDetail={handleViewLogDetail}
        />
      );
    }
    if (activeTab === "firewall-config") return <AIMeshFirewallConfig />;
    return <AIMeshFirewallOverview onTabChange={handleTabChange} />;
  };

  return (
    <DashboardLayout activeTab={activeTab} onTabChange={handleTabChange}>
      {renderContent()}
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
        path="/"
        element={
          <ProtectedRoute>
            <DashboardApp />
          </ProtectedRoute>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
