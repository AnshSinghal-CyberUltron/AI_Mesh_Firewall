/**
 * OAuthCallback — Landing page for OAuth provider redirects.
 *
 * Linear (and other OAuth providers) redirect the user's browser back to
 * `{origin}/oauth/callback?code=...&state=...` after authorization.
 * This page simply renders a waiting indicator so that:
 *
 *   1. The URL (with 'code' and 'state' query params) is preserved — the
 *      React Router wildcard (`path="*"`) would otherwise redirect to "/" and
 *      destroy the params before MCPManagerPanel.handleOAuthPopup() can read them.
 *   2. The parent window's polling interval can read `popup.location.href`,
 *      detect same-origin access, and extract the params without a race condition.
 *
 * No API calls are made here. All token exchange logic lives in the parent window.
 */
export function OAuthCallback() {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "100vh",
        gap: "16px",
        fontFamily: "system-ui, sans-serif",
        background: "#0f172a",
        color: "#f1f5f9",
      }}
    >
      <svg
        width="48"
        height="48"
        viewBox="0 0 24 24"
        fill="none"
        stroke="#6366f1"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{ animation: "spin 1s linear infinite" }}
      >
        <path d="M21 12a9 9 0 1 1-6.219-8.56" />
      </svg>
      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
      <p style={{ fontSize: "18px", fontWeight: 600, margin: 0 }}>
        Completing authorization&hellip;
      </p>
      <p style={{ fontSize: "14px", color: "#94a3b8", margin: 0 }}>
        You can close this window if it does not close automatically.
      </p>
    </div>
  );
}
