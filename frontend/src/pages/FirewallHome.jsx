import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { Profile } from "./Profile";
import { Settings } from "./Settings";
import { AIMeshFirewallOverview } from "./AIMeshFirewallOverview";
import {
  Firewall11Page,
  Firewall12Page,
  Firewall13Page,
  Firewall14Page,
  Firewall15Page,
  Firewall16Page,
  Firewall17Page,
} from "./firewall-submodules";
import { AIMeshFirewallConfig } from "./AIMeshFirewallConfig";
import { SubModuleResultsPage } from "../components/SubmoduleResultsPage";
import { LogDetailPage } from "../components/LogDetailPage";

const firewallTabToModuleId = {
  "firewall-1-1": "1.1",
  "firewall-1-2": "1.2",
  "firewall-1-3": "1.3",
  "firewall-1-4": "1.4",
  "firewall-1-5": "1.5",
  "firewall-1-6": "1.6",
  "firewall-1-7": "1.7",
};

export function FirewallHome({ onTabChange }) {
  const { user } = useAuth();
  const [searchParams] = useSearchParams();
  const activeTab = searchParams.get("tab") || "firewall";
  const [resultsTab, setResultsTab] = useState(null);
  const [logDetailData, setLogDetailData] = useState(null);
  const tabInitializedRef = useRef(false);

  useEffect(() => {
    if (!user || tabInitializedRef.current) return;
    tabInitializedRef.current = true;
  }, [user]);

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
    return <AIMeshFirewallOverview onTabChange={onTabChange} />;
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
  return <AIMeshFirewallOverview onTabChange={onTabChange} />;
}
