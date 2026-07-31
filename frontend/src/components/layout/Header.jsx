import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Search, Bell, HelpCircle, User, Plus, Moon, Sun, X, ExternalLink, CheckCircle2, AlertTriangle, Settings, LogOut, Menu } from "lucide-react";
import { Button } from "../ui/Button";
import { Badge } from "../ui/Badge";
import { useAuth } from "../../context/AuthContext";
import { useTheme } from "../../context/ThemeContext";
import { useRealtimeNotifications } from "../../hooks/useRealtimeNotifications";

function useOfferingVisibility(user) {
  const roles = user?.roles || [];
  const hasPlatform =
    user?.is_superuser || roles.some((r) => ["platform_admin", "platform_user"].includes(r));
  return {
    hasPlatform: hasPlatform || !roles.length,
  };
}

function formatRelativeTime(isoString) {
  if (!isoString) return "";
  try {
    const diff = Date.now() - new Date(isoString).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "Just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  } catch {
    return "";
  }
}

function HelpModal({ open, onClose }) {
  if (!open) return null;
  const docsUrl = `${window.location.origin.replace(/:\d+$/, ":8100")}/docs/`;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="bg-white dark:bg-slate-800 rounded-2xl shadow-2xl border border-slate-200 dark:border-slate-700 w-full max-w-md mx-4">
        <div className="flex items-center justify-between p-6 border-b border-slate-100 dark:border-slate-700">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-teal-100 dark:bg-teal-800/30 dark:bg-teal-900/50 rounded-xl flex items-center justify-center">
              <HelpCircle className="w-5 h-5 text-teal-600 dark:text-teal-400" />
            </div>
            <h3 className="text-lg font-semibold text-slate-900 dark:text-slate-100">Help</h3>
          </div>
          <button
            onClick={onClose}
            aria-label="Close help"
            className="p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-700 dark:hover:bg-slate-700 transition-colors text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:text-slate-300 dark:text-slate-400 dark:hover:text-slate-200"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="p-6 space-y-4">
          <p className="text-sm text-slate-600 dark:text-slate-400 dark:text-slate-300">
            ZeroShield is an AI Mesh Firewall for securing and governing LLM interactions.
          </p>
          <ul className="text-sm text-slate-600 dark:text-slate-400 dark:text-slate-300 space-y-2 list-disc list-inside">
            <li>Use the sidebar to switch between Dashboard, User Management, and modules.</li>
            <li>Platform users can access all firewall modules from the AI Mesh section.</li>
            <li>Admins can manage users and policy controls from the control console.</li>
          </ul>
          <a
            href={docsUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 text-sm font-medium text-teal-600 hover:text-teal-700 dark:text-teal-400 dark:hover:text-teal-300"
          >
            <ExternalLink className="w-4 h-4" />
            Open API documentation
          </a>
        </div>
        <div className="px-6 pb-6">
          <Button variant="outline" size="sm" onClick={onClose} className="w-full">
            Close
          </Button>
        </div>
      </div>
    </div>
  );
}

function NotificationDropdown({ notifications, onMarkRead, onMarkAllRead, onClose }) {
  const unread = notifications.filter((n) => !n.read).length;
  return (
    <div className="absolute right-0 top-full mt-2 w-80 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-xl z-50">
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100 dark:border-slate-700">
        <span className="text-sm font-semibold text-slate-900 dark:text-slate-100">
          Notifications {unread > 0 && <span className="text-amber-600 dark:text-amber-400">({unread} unread)</span>}
        </span>
        <div className="flex items-center gap-2">
          {unread > 0 && (
            <button
              onClick={onMarkAllRead}
              className="text-xs text-teal-600 hover:text-teal-700 dark:text-teal-400 dark:hover:text-teal-300 font-medium"
            >
              Mark all read
            </button>
          )}
          <button onClick={onClose} aria-label="Close notifications" className="p-1 rounded hover:bg-slate-100 dark:hover:bg-slate-700 dark:hover:bg-slate-700 text-slate-400">
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      <div className="max-h-80 overflow-y-auto">
        {notifications.length === 0 ? (
          <div className="px-4 py-6 text-center text-sm text-slate-500 dark:text-slate-400">No notifications</div>
        ) : (
          notifications.map((n) => (
            <div
              key={n.id}
              className={`px-4 py-3 border-b border-slate-50 dark:border-slate-700/50 hover:bg-slate-50 dark:hover:bg-slate-700/50 transition-colors ${!n.read ? "bg-amber-50/40 dark:bg-amber-900/10" : ""}`}
            >
              <div className="flex items-start gap-3">
                <div className="mt-0.5 flex-shrink-0">
                  {n.type === "escalation" ? (
                    <AlertTriangle className="w-4 h-4 text-amber-500" />
                  ) : (
                    <CheckCircle2 className="w-4 h-4 text-green-500" />
                  )}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium text-slate-900 dark:text-slate-100 leading-snug">{n.message || n.incident_title}</p>
                  <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">{formatRelativeTime(n.created_at)}</p>
                </div>
                {!n.read && (
                  <button
                    onClick={() => onMarkRead(n.id)}
                    aria-label="Mark notification as read"
                    className="flex-shrink-0 text-xs text-slate-400 hover:text-teal-600 dark:hover:text-teal-400"
                    title="Mark as read"
                  >
                    <CheckCircle2 className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

const TAB_TITLES = {
  dashboard: "Dashboard",
  "user-management": "User Management",
  firewall: "AI Mesh Firewall Overview",
  "firewall-1-1": "AI Gateway & Traffic Ingress",
  "firewall-1-2": "Policy Management",
  "firewall-1-3": "RAG & Vector DB Firewall",
  "firewall-1-4": "Context Assembly & MCP Guardrails",
  "firewall-1-5": "Multi-Model Governance",
  "firewall-1-6": "Model Isolation & Kill-Switch",
  "firewall-1-7": "Output Guardrails",
  "firewall-config": "Module 1 Inputs",
  "m2-dashboard": "Gateway Intelligence Hub",
  "m2-ueba-api-keys": "API Key & Identity Risk",
  "m2-threat-intel": "Threat Intelligence Ops",
  "m2-models-exposure": "Model & RAG Health",
  "m2-mcp-risk": "MCP & Context Risk",
  "m2-incidents": "Incidents & Forensics",
};

export function Header({ activeTab, searchQuery = "", onSearchQueryChange, onSearchSubmit, onTabChange, onMobileMenuToggle }) {
  const { user, logout, fetchWithAuth } = useAuth();
  const { resolvedTheme, toggleTheme } = useTheme();
  const isAdmin = user?.is_superuser || (user?.roles || []).includes("platform_admin");
  const { hasPlatform } = useOfferingVisibility(user);
  const navigate = useNavigate();
  const displayName = user ? [user.first_name, user.last_name].filter(Boolean).join(' ') || user.email : '';
  const [showHelpModal, setShowHelpModal] = useState(false);
  const [localQuery, setLocalQuery] = useState(searchQuery);
  const [showNotifications, setShowNotifications] = useState(false);
  const [showUserDropdown, setShowUserDropdown] = useState(false);
  const [notifications, setNotifications] = useState([]);
  const bellRef = useRef(null);
  const userDropdownRef = useRef(null);

  const notificationsFetchedAtRef = useRef(0);

  const fetchNotifications = useCallback(async (force = false) => {
    if (!isAdmin || !user) return;
    const now = Date.now();
    if (!force && now - notificationsFetchedAtRef.current < 60_000) return;
    try {
      const res = await fetchWithAuth("/api/notifications/?limit=30");
      if (res.ok) {
        const data = await res.json();
        setNotifications(Array.isArray(data) ? data : []);
        notificationsFetchedAtRef.current = now;
      }
    } catch {
      // non-critical
    }
  }, [fetchWithAuth, isAdmin, user]);

  useEffect(() => {
    fetchNotifications();
  }, [fetchNotifications]);

  // Close notification dropdown when clicking outside
  useEffect(() => {
    if (!showNotifications) return;
    const handler = (e) => {
      if (bellRef.current && !bellRef.current.contains(e.target)) {
        setShowNotifications(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showNotifications]);

  // Close user dropdown when clicking outside
  useEffect(() => {
    if (!showUserDropdown) return;
    const handler = (e) => {
      if (userDropdownRef.current && !userDropdownRef.current.contains(e.target)) {
        setShowUserDropdown(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showUserDropdown]);

  useRealtimeNotifications({
    enabled: isAdmin,
    onEscalationEvent: () => fetchNotifications(true),
    onResolutionEvent: () => fetchNotifications(true),
  });

  const handleMarkRead = useCallback(async (id) => {
    try {
      await fetchWithAuth(`/api/notifications/${id}/read/`, { method: "POST" });
      setNotifications((prev) => prev.map((n) => n.id === id ? { ...n, read: true } : n));
    } catch {
      // non-critical
    }
  }, [fetchWithAuth]);

  const handleMarkAllRead = useCallback(async () => {
    try {
      await fetchWithAuth("/api/notifications/mark-all-read/", { method: "POST" });
      setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
    } catch {
      // non-critical
    }
  }, [fetchWithAuth]);

  const unreadCount = notifications.filter((n) => !n.read).length;

  const handleLogout = async () => {
    setShowUserDropdown(false);
    await logout();  // Wait for logout to complete before navigating
    navigate('/login');
  };

  const handleSearchKeyDown = (e) => {
    if (e.key === "Enter") {
      const q = (onSearchQueryChange ? searchQuery : localQuery).trim();
      if (onSearchSubmit) onSearchSubmit(q);
      else setLocalQuery("");
    }
  };

  const searchValue = onSearchQueryChange !== undefined ? searchQuery : localQuery;
  const setSearchValue = onSearchQueryChange || setLocalQuery;
  const pageTitle =
    TAB_TITLES[activeTab]
    || (activeTab?.startsWith("m2-") ? "Gateway Behaviour Intelligence" : "Control Console");
  const environment = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1" ? "DEV" : "PROD";

  const isDark = resolvedTheme === "dark";
  const ThemeIcon = isDark ? Sun : Moon;

  return (
    <>
      <header className="border-b border-slate-200 bg-white transition-colors dark:border-slate-700 dark:bg-slate-900">
        <div className="flex items-center justify-between px-3 py-3 md:px-6">
          <div className="flex-1 max-w-3xl">
            <div className="mb-2 flex items-center gap-2">
              <Button
                variant="ghost"
                size="icon"
                className="text-slate-600 hover:text-slate-900 lg:hidden dark:text-slate-400 dark:hover:text-slate-100"
                onClick={onMobileMenuToggle}
                aria-label="Open navigation"
                title="Open navigation"
              >
                <Menu className="h-5 w-5" />
              </Button>
              <div className="min-w-0 flex flex-wrap items-center gap-2 text-xs">
                <span className="font-semibold uppercase tracking-[0.14em] text-slate-500 dark:text-slate-400">{pageTitle}</span>
                <span className="rounded-full border border-slate-200 px-2 py-0.5 font-medium text-slate-600 dark:border-slate-700 dark:text-slate-300">{environment}</span>
                <span className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 font-medium text-emerald-700 dark:border-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                  Connected
                </span>
              </div>
            </div>
            <div className="relative">
              <Search
                className={`absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 ${
                  isDark ? "text-slate-500" : "text-slate-400"
                }`}
              />
              <input
                type="text"
                value={searchValue}
                onChange={(e) => setSearchValue(e.target.value)}
                onKeyDown={handleSearchKeyDown}
                placeholder="Search models, policies, threats, or infrastructure..."
                className={`w-full rounded-lg py-2 pl-10 pr-4 text-sm transition-all focus:border-transparent focus:outline-none focus:ring-2 focus:ring-teal-500 ${
                  isDark
                    ? "border border-slate-700 bg-slate-800 text-slate-100 placeholder:text-slate-500 focus:bg-slate-700"
                    : "border border-slate-200 bg-white text-slate-900 placeholder:text-slate-500 focus:bg-white"
                }`}
              />
            </div>
          </div>

          <div className="ml-3 flex items-center gap-1.5 md:ml-6 md:gap-3">
            {hasPlatform && (
              <>
                <Button
                  className="hidden gap-2 bg-gradient-to-r from-teal-600 to-cyan-600 text-white shadow-sm hover:from-teal-700 hover:to-cyan-700 md:inline-flex"
                  size="sm"
                  onClick={() => onTabChange?.('firewall-1-1')}
                >
                  <Plus className="w-4 h-4" />
                  <span>Scan Model</span>
                </Button>
                <div className="hidden h-6 w-px bg-slate-200 dark:bg-slate-700 md:block" />
              </>
            )}

            <Button
              variant="ghost"
              size="icon"
              aria-label="Help"
              title="Help"
              className="relative text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100"
              onClick={() => setShowHelpModal(true)}
            >
              <HelpCircle className="w-5 h-5" />
            </Button>

            {/* Bell with real notifications for admins */}
            <div className="relative" ref={bellRef}>
              <Button
                variant="ghost"
                size="icon"
                aria-label={unreadCount > 0 ? `Notifications (${unreadCount} unread)` : "Notifications"}
                title="Notifications"
                className="relative text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100"
                onClick={() => {
                  if (isAdmin) setShowNotifications((v) => !v);
                }}
              >
                <Bell className="w-5 h-5" />
                {unreadCount > 0 && (
                  <Badge className="absolute -top-1 -right-1 w-5 h-5 p-0 flex items-center justify-center bg-red-500 text-white text-xs border-2 border-white dark:border-slate-900">
                    {unreadCount > 9 ? "9+" : unreadCount}
                  </Badge>
                )}
              </Button>

              {isAdmin && showNotifications && (
                <NotificationDropdown
                  notifications={notifications}
                  onMarkRead={handleMarkRead}
                  onMarkAllRead={handleMarkAllRead}
                  onClose={() => setShowNotifications(false)}
                />
              )}
            </div>

            <div className="hidden h-6 w-px bg-slate-200 dark:bg-slate-700 md:block" />

            <Button
              variant="ghost"
              size="icon"
              className="text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100"
              onClick={toggleTheme}
              aria-label={resolvedTheme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
              title={resolvedTheme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            >
              <ThemeIcon className="w-5 h-5 transition-transform duration-300" />
            </Button>

            <div className="hidden h-6 w-px bg-slate-200 dark:bg-slate-700 md:block" />

            {/* User dropdown */}
            <div className="relative" ref={userDropdownRef}>
              <button
                onClick={() => setShowUserDropdown((v) => !v)}
                className="flex items-center gap-2.5 rounded-lg px-2 py-1.5 transition-colors hover:bg-slate-50 dark:bg-slate-900 dark:hover:bg-slate-800"
              >
                <div className="hidden text-right md:block">
                  <p className="text-xs font-medium text-slate-900 dark:text-slate-100">{displayName || "User"}</p>
                  <p className="text-xs text-slate-500 dark:text-slate-300">{user?.email || ""}</p>
                </div>
                <div className="w-8 h-8 bg-gradient-to-br from-teal-500 to-cyan-600 rounded-full flex items-center justify-center ring-2 ring-slate-100 dark:ring-slate-700">
                  <User className="w-4 h-4 text-white" />
                </div>
              </button>

              {showUserDropdown && (
                <div className="absolute right-0 top-full mt-2 w-48 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-xl z-50 overflow-hidden">
                  <button
                    onClick={() => { setShowUserDropdown(false); onTabChange?.('profile'); }}
                    className="w-full flex items-center gap-3 px-4 py-3 text-sm text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors"
                  >
                    <User className="w-4 h-4 text-slate-500 dark:text-slate-400" />
                    <span>Profile</span>
                  </button>
                  <button
                    onClick={() => { setShowUserDropdown(false); onTabChange?.('settings'); }}
                    className="w-full flex items-center gap-3 px-4 py-3 text-sm text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors"
                  >
                    <Settings className="w-4 h-4 text-slate-500 dark:text-slate-400" />
                    <span>Settings</span>
                  </button>
                  <div className="border-t border-slate-100 dark:border-slate-700"></div>
                  <button
                    onClick={handleLogout}
                    className="w-full flex items-center gap-3 px-4 py-3 text-sm text-red-600 dark:text-red-400 hover:bg-red-50 dark:bg-red-900/20 dark:hover:bg-red-900/20 transition-colors"
                  >
                    <LogOut className="w-4 h-4" />
                    <span>Logout</span>
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      </header>

      <HelpModal open={showHelpModal} onClose={() => setShowHelpModal(false)} />
    </>
  );
}
