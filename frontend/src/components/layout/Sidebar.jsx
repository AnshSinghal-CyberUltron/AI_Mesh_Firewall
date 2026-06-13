import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  Shield,
  ShieldAlert,
  LayoutDashboard,
  ChevronLeft,
  ChevronRight,
  User,
  Settings,
  LogOut,
  ChevronUp,
  ChevronDown,
  X,
} from "lucide-react";
import { cn } from "../../lib/utils";
import { useAuth } from "../../context/AuthContext";

const menuItems = [
  {
    id: "firewall",
    label: "AI Mesh Firewall",
    icon: ShieldAlert,
    section: "Product",
    offering: "platform",
    subItems: [
      { id: "firewall-1-1", label: "AI Gateway & Traffic Ingress" },
      { id: "firewall-1-2", label: "Policy Management" },
      { id: "firewall-1-3", label: "RAG & Vector DB Firewall" },
      { id: "firewall-1-4", label: "Context Assembly & MCP" },
      { id: "firewall-1-5", label: "Multi-Model Governance" },
      { id: "firewall-1-6", label: "Model Isolation & Kill-Switch" },
      { id: "firewall-1-7", label: "Output Guardrails" },
      { id: "firewall-config", label: "Inputs" },
    ],
  },
  {
    id: "module2",
    label: "Gateway Behaviour Intelligence",
    icon: LayoutDashboard,
    section: "Module 2",
    offering: "platform",
    subItems: [
      { id: "m2-dashboard", label: "M2.1 Gateway Intelligence Hub", route: "/dashboard" },
      { id: "m2-ueba-api-keys", label: "M2.2 API Key & Identity Risk", route: "/ueba/api-keys" },
      { id: "m2-models-exposure", label: "M2.3 Model & RAG Health", route: "/models/exposure" },
      { id: "m2-mcp-risk", label: "M2.4 MCP & Context Risk", route: "/mcp/risk" },
      { id: "m2-threat-intel", label: "M2.5 Threat Intelligence Ops", route: "/threat-intel" },
      { id: "m2-incidents", label: "M2.6 Incidents & Forensics", route: "/incidents" },
    ],
  },
];

function useOfferingVisibility(user) {
  const roles = user?.roles || [];
  const hasPlatform =
    user?.is_superuser || roles.some((r) => ["platform_admin", "platform_user"].includes(r));
  return {
    hasPlatform: hasPlatform || !roles.length,
  };
}

export function Sidebar({ activeTab, onTabChange, mobileOpen = false, onCloseMobile }) {
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [hasManualCollapsePreference, setHasManualCollapsePreference] = useState(false);
  const [showAccountMenu, setShowAccountMenu] = useState(false);
  const [flyoutModule, setFlyoutModule] = useState(null);
  const { user, logout } = useAuth();
  const { hasPlatform } = useOfferingVisibility(user);
  const [expandedModules, setExpandedModules] = useState([]);
  const [hasCustomizedExpansion, setHasCustomizedExpansion] = useState(false);
  const isAdmin = user?.is_superuser || (user?.roles || []).includes("platform_admin");
  const navigate = useNavigate();
  const displayName = user ? [user.first_name, user.last_name].filter(Boolean).join(' ') || user.email : '';

  const visibleMenuItems = menuItems.filter((item) => {
    const offering = item.offering || "platform";
    return offering === "platform" && hasPlatform;
  });

  const effectiveExpandedModules = hasCustomizedExpansion
    ? expandedModules
    : Array.from(
        new Set([
          ...expandedModules,
          ...visibleMenuItems
            .filter((item) => item.subItems && (
              activeTab === item.id
              || activeTab.startsWith(item.id)
              || (item.id === "module2" && activeTab.startsWith("m2-"))
            ))
            .map((item) => item.id),
        ])
      );

  useEffect(() => {
    const syncCollapseForViewport = () => {
      if (hasManualCollapsePreference) return;
      if (window.innerWidth < 1024) {
        setIsCollapsed(false);
        return;
      }
      // Keep sidebar compact on smaller desktops and expanded on wider screens.
      setIsCollapsed(window.innerWidth < 1360);
    };

    syncCollapseForViewport();
    window.addEventListener("resize", syncCollapseForViewport);
    return () => window.removeEventListener("resize", syncCollapseForViewport);
  }, [hasManualCollapsePreference]);

  useEffect(() => {
    if (!mobileOpen) return undefined;

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const handleEscape = (event) => {
      if (event.key === "Escape") {
        onCloseMobile?.();
      }
    };

    window.addEventListener("keydown", handleEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleEscape);
    };
  }, [mobileOpen, onCloseMobile]);

  const handleLogout = async () => {
    setShowAccountMenu(false);
    onCloseMobile?.();
    await logout();  // Wait for logout to complete before navigating
    navigate('/login');
  };

  const handleMenuItemClick = (id, route) => {
    if (route) {
      navigate(route);
    } else if (id === "firewall") {
      navigate("/");
      onTabChange?.(id);
    } else if (id.startsWith("firewall")) {
      navigate(`/?tab=${id}`);
      onTabChange?.(id);
    } else {
      onTabChange?.(id);
    }
    setShowAccountMenu(false);
    onCloseMobile?.();
  };

  const overviewModules = ['firewall', 'module2'];

  const toggleModule = (id) => {
    setHasCustomizedExpansion(true);
    if (effectiveExpandedModules.includes(id)) {
      setExpandedModules(effectiveExpandedModules.filter(m => m !== id));
    } else {
      setExpandedModules([...effectiveExpandedModules, id]);
    }
    if (overviewModules.includes(id)) {
      onTabChange(id);
    }
  };

  return (
    <aside
      className={cn(
        "fixed inset-y-0 left-0 z-40 w-[86vw] max-w-80 bg-white dark:bg-slate-900 border-r border-slate-200 dark:border-slate-700 flex flex-col transition-transform duration-300 lg:static lg:translate-x-0 lg:z-auto lg:h-screen lg:w-72 lg:max-w-none",
        mobileOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0",
        isCollapsed ? "lg:w-20" : "lg:w-72"
      )}
    >
      <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-700 lg:hidden">
        <div className="flex items-center gap-2">
          <Shield className="h-4 w-4 text-teal-600 dark:text-teal-400" />
          <span className="text-sm font-semibold text-slate-900 dark:text-slate-100">Navigation</span>
        </div>
        <button
          type="button"
          onClick={() => onCloseMobile?.()}
          className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-slate-200 text-slate-600 transition-colors hover:bg-slate-100 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
          aria-label="Close navigation"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Header */}
      <div className="relative border-b border-slate-200 dark:border-slate-700">
        <div className={cn("p-6", isCollapsed && "p-4", "hidden lg:block")}>
          <div className={cn("flex items-center", isCollapsed ? "justify-center" : "gap-3")}>
            <div className="relative flex-shrink-0">
              <div className="w-10 h-10 bg-gradient-to-br from-cyan-500 to-teal-600 rounded-lg flex items-center justify-center shadow-sm">
                <Shield className="w-5 h-5 text-white" strokeWidth={2.5} />
              </div>
              <div className="absolute -bottom-0.5 -right-0.5 w-3 h-3 bg-teal-500 rounded-full border-2 border-white dark:border-slate-900"></div>
            </div>
            {!isCollapsed && (
              <div className="min-w-0 flex-1">
                <h1 className="text-sm font-semibold text-slate-900 dark:text-slate-100 tracking-tight">
                  ZeroShield
                </h1>
                <p className="text-xs text-slate-500 dark:text-slate-400 truncate">AI Security Platform</p>
              </div>
            )}
          </div>
        </div>

        {/* Collapse Toggle */}
        <button
          onClick={() => {
            setHasManualCollapsePreference(true);
            setIsCollapsed(!isCollapsed);
          }}
          className="absolute -right-3 top-1/2 -translate-y-1/2 hidden h-6 w-6 items-center justify-center rounded-full border border-slate-200 bg-white shadow-sm transition-colors hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:hover:bg-slate-700 lg:flex"
          title={isCollapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {isCollapsed ? (
            <ChevronRight className="w-3.5 h-3.5 text-slate-600 dark:text-slate-400" />
          ) : (
            <ChevronLeft className="w-3.5 h-3.5 text-slate-600 dark:text-slate-400" />
          )}
        </button>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-3 space-y-1 overflow-y-auto">
        {visibleMenuItems.map((item) => (
          <div key={item.id}>
            {item.section && !isCollapsed && (
              <div className="px-3 pt-4 pb-2">
                <span className="text-xs font-semibold text-slate-400 dark:text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                  {item.section}
                </span>
              </div>
            )}

            {item.subItems && !isCollapsed ? (
              <div>
                <button
                  onClick={() => toggleModule(item.id)}
                  className={cn(
                    "w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-all duration-200 group relative",
                    (activeTab === item.id || activeTab.startsWith(item.id) || (item.id === "module2" && activeTab.startsWith("m2-")))
                      ? "bg-gradient-to-r from-cyan-50 dark:from-cyan-900/20 to-teal-50 dark:to-teal-900/20 dark:from-teal-900/30 dark:to-cyan-900/20 text-teal-700 dark:text-teal-400 font-medium shadow-sm"
                      : "text-slate-600 dark:text-slate-400 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700 dark:bg-slate-900 dark:hover:bg-slate-800 hover:text-slate-900 dark:text-slate-100 dark:hover:text-slate-100"
                  )}
                >
                  <item.icon
                    className={cn(
                      "flex-shrink-0 transition-transform group-hover:scale-110",
                      (activeTab === item.id || activeTab.startsWith(item.id) || (item.id === "module2" && activeTab.startsWith("m2-"))) ? "w-5 h-5" : "w-4 h-4"
                    )}
                    strokeWidth={(activeTab === item.id || activeTab.startsWith(item.id) || (item.id === "module2" && activeTab.startsWith("m2-"))) ? 2.5 : 2}
                  />
                  <span className="truncate flex-1 text-left">{item.label}</span>
                  <ChevronDown
                    className={cn(
                      "w-4 h-4 transition-transform",
                      effectiveExpandedModules.includes(item.id) && "rotate-180"
                    )}
                  />
                </button>

                {effectiveExpandedModules.includes(item.id) && (
                  <div className="ml-8 mt-1 space-y-1">
                    {item.subItems
                      .filter((subItem) => !subItem.adminOnly || isAdmin)
                      .map((subItem) => (
                      <button
                        key={subItem.id}
                        onClick={() => handleMenuItemClick(subItem.id, subItem.route)}
                        className={cn(
                          "w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-all duration-200 border-l-2",
                          activeTab === subItem.id
                            ? "bg-teal-50 dark:bg-teal-900/30 text-teal-700 dark:text-teal-400 font-semibold border-teal-500 dark:border-teal-400"
                            : "text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700 border-transparent"
                        )}
                      >
                        <div className={cn(
                          "w-1.5 h-1.5 rounded-full flex-shrink-0",
                          activeTab === subItem.id ? "bg-teal-600 dark:bg-teal-400" : "bg-slate-400 dark:bg-slate-600"
                        )}></div>
                        <span className="truncate">{subItem.label}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <div className="relative"
                onMouseEnter={() => isCollapsed && item.subItems && setFlyoutModule(item.id)}
                onMouseLeave={() => setFlyoutModule(null)}
              >
                <button
                  onClick={() => {
                    if (isCollapsed && item.subItems) {
                      setFlyoutModule(flyoutModule === item.id ? null : item.id);
                    } else {
                      handleMenuItemClick(item.id);
                    }
                  }}
                  className={cn(
                    "w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-all duration-200 group relative",
                    activeTab === item.id || (item.subItems && activeTab.startsWith(item.id))
                      ? "bg-gradient-to-r from-cyan-50 dark:from-cyan-900/20 to-teal-50 dark:to-teal-900/20 dark:from-teal-900/30 dark:to-cyan-900/20 text-teal-700 dark:text-teal-400 font-medium shadow-sm"
                      : "text-slate-600 dark:text-slate-400 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700 dark:bg-slate-900 dark:hover:bg-slate-800 hover:text-slate-900 dark:text-slate-100 dark:hover:text-slate-100",
                    isCollapsed && "justify-center"
                  )}
                  title={isCollapsed ? item.label : undefined}
                >
                  <item.icon
                    className={cn(
                      "flex-shrink-0 transition-transform group-hover:scale-110",
                      (activeTab === item.id || (item.subItems && activeTab.startsWith(item.id))) ? "w-5 h-5" : "w-4 h-4"
                    )}
                    strokeWidth={(activeTab === item.id || (item.subItems && activeTab.startsWith(item.id))) ? 2.5 : 2}
                  />
                  {!isCollapsed && <span className="truncate">{item.label}</span>}

                  {activeTab === item.id && !isCollapsed && (
                    <div className="ml-auto w-1.5 h-1.5 rounded-full bg-teal-500"></div>
                  )}
                </button>

                {/* Flyout menu for collapsed sidebar with sub-items */}
                {isCollapsed && item.subItems && flyoutModule === item.id && (
                  <div className="absolute left-full top-0 ml-2 w-56 bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 shadow-xl z-50 py-1.5 animate-slideIn">
                    <div className="px-3 py-2 border-b border-slate-100 dark:border-slate-700/50 mb-1">
                      <p className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">{item.label}</p>
                    </div>
                    {item.subItems
                      .filter((subItem) => !subItem.adminOnly || isAdmin)
                      .map((subItem) => (
                      <button
                        key={subItem.id}
                        onClick={() => { handleMenuItemClick(subItem.id, subItem.route); setFlyoutModule(null); }}
                        className={cn(
                          "w-full flex items-center gap-2 px-3 py-2 text-sm transition-colors",
                          activeTab === subItem.id
                            ? "bg-teal-50 dark:bg-teal-900/20 dark:bg-teal-900/50 text-teal-700 dark:text-teal-400 font-medium"
                            : "text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-700"
                        )}
                      >
                        <div className={cn(
                          "w-1.5 h-1.5 rounded-full flex-shrink-0",
                          activeTab === subItem.id ? "bg-teal-600 dark:bg-teal-400" : "bg-slate-400 dark:bg-slate-600"
                        )}></div>
                        <span className="truncate">{subItem.label}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
      </nav>

      {/* Status Indicator */}
      {!isCollapsed && (
        <div className="hidden p-4 pt-0 lg:block">
          <div className="relative bg-gradient-to-br from-slate-50 dark:from-slate-900 to-slate-100 dark:from-slate-800 dark:to-slate-800/50 p-4 rounded-lg border border-slate-200 dark:border-slate-700 overflow-hidden">
            <div className="absolute top-0 right-0 w-20 h-20 bg-gradient-to-br from-cyan-400/20 to-teal-400/20 rounded-full blur-2xl"></div>
            <div className="relative">
              <h3 className="text-xs font-semibold text-slate-900 dark:text-slate-100 mb-1">System Status</h3>
              <p className="text-xs text-slate-600 dark:text-slate-400 mb-3">All systems operational</p>
              <div className="flex items-center gap-2">
                <div className="w-1.5 h-1.5 bg-teal-500 rounded-full animate-pulse"></div>
                <span className="text-xs font-medium text-teal-700 dark:text-teal-400">Protected</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Account Section */}
      <div className="relative border-t border-slate-200 dark:border-slate-700">
        {showAccountMenu && !isCollapsed && (
          <div className="absolute bottom-full left-4 right-4 mb-2 bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 shadow-lg overflow-hidden z-50">
            <button
              onClick={() => { onTabChange('profile'); setShowAccountMenu(false); }}
              className="w-full flex items-center gap-3 px-4 py-3 text-sm text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors"
            >
              <User className="w-4 h-4 text-slate-500 dark:text-slate-400" />
              <span>Profile</span>
            </button>
            <button
              onClick={() => { onTabChange('settings'); setShowAccountMenu(false); }}
              className="w-full flex items-center gap-3 px-4 py-3 text-sm text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors"
            >
              <Settings className="w-4 h-4 text-slate-500 dark:text-slate-400" />
              <span>Settings</span>
            </button>
            <div className="border-t border-slate-100 dark:border-slate-700"></div>
            <button onClick={handleLogout} className="w-full flex items-center gap-3 px-4 py-3 text-sm text-red-600 dark:text-red-400 hover:bg-red-50 dark:bg-red-900/20 dark:hover:bg-red-900/20 transition-colors">
              <LogOut className="w-4 h-4" />
              <span>Logout</span>
            </button>
          </div>
        )}

        <button
          onClick={() => !isCollapsed && setShowAccountMenu(!showAccountMenu)}
          className={cn(
            "w-full p-4 hover:bg-slate-50 dark:hover:bg-slate-700 dark:bg-slate-900 dark:hover:bg-slate-800 transition-colors group",
            isCollapsed && "flex justify-center"
          )}
        >
          <div className={cn("flex items-center", isCollapsed ? "justify-center" : "gap-3")}>
            <div className="w-9 h-9 bg-gradient-to-br from-teal-500 to-cyan-600 rounded-full flex items-center justify-center ring-2 ring-slate-100 dark:ring-slate-700 flex-shrink-0">
              <User className="w-4 h-4 text-white" />
            </div>
            {!isCollapsed && (
              <>
                <div className="text-left min-w-0 flex-1">
                  <p className="text-sm font-medium text-slate-900 dark:text-slate-100 truncate">{displayName || 'User'}</p>
                  <p className="text-xs text-slate-500 dark:text-slate-400 truncate">{user?.email || ''}</p>
                </div>
                <ChevronUp
                  className={cn(
                    "w-4 h-4 text-slate-400 dark:text-slate-500 dark:text-slate-400 transition-transform flex-shrink-0",
                    showAccountMenu && "rotate-180"
                  )}
                />
              </>
            )}
          </div>
        </button>
      </div>

      {showAccountMenu && !isCollapsed && (
        <div
          className="fixed inset-0 z-40"
          onClick={() => setShowAccountMenu(false)}
        />
      )}
    </aside>
  );
}
