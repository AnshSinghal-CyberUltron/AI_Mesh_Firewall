import { useEffect, useRef, useState } from "react";
import { Sidebar } from "./Sidebar";
import { Header } from "./Header";
import { InactivityWarningModal } from "../InactivityWarningModal";
import { useInactivityLogout } from "../../hooks/useInactivityLogout";

export function DashboardLayout({
  children,
  activeTab,
  onTabChange,
  searchQuery,
  onSearchQueryChange,
  onSearchSubmit,
}) {
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const contentRef = useRef(null);
  const { showWarning, stayLoggedIn } = useInactivityLogout(
    15 * 60 * 1000,
    60 * 1000
  );

  useEffect(() => {
    if (!contentRef.current) return;
    contentRef.current.scrollTo({ top: 0, left: 0, behavior: "auto" });
  }, [activeTab]);

  const handleTabChange = (tab) => {
    onTabChange?.(tab);
    setMobileSidebarOpen(false);
  };

  return (
    <div className="zs-app-shell flex h-screen overflow-hidden">
      <Sidebar
        activeTab={activeTab}
        onTabChange={handleTabChange}
        mobileOpen={mobileSidebarOpen}
        onCloseMobile={() => setMobileSidebarOpen(false)}
      />
      {mobileSidebarOpen && (
        <button
          type="button"
          aria-label="Close navigation"
          onClick={() => setMobileSidebarOpen(false)}
          className="fixed inset-0 z-30 bg-slate-950/45 backdrop-blur-[1px] lg:hidden"
        />
      )}
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header
          activeTab={activeTab}
          searchQuery={searchQuery}
          onSearchQueryChange={onSearchQueryChange}
          onSearchSubmit={onSearchSubmit}
          onTabChange={handleTabChange}
          onMobileMenuToggle={() => setMobileSidebarOpen((open) => !open)}
        />
        <main ref={contentRef} className="flex-1 overflow-y-auto px-3 py-4 sm:px-4 sm:py-5 md:px-6 lg:px-8">
          <div className="mx-auto w-full max-w-[1700px]">{children}</div>
        </main>
      </div>
      <InactivityWarningModal
        open={showWarning}
        onStayLoggedIn={stayLoggedIn}
      />
    </div>
  );
}

