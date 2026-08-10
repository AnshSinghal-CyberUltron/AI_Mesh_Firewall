/**
 * Offering visibility for the console shell.
 *
 * Historical note: an older multi-offering gate only treated
 * platform_admin/platform_user (or empty roles) as visible. Backend /api/auth/me/
 * often returns roles=["staff","user"] for operator accounts — that emptied the
 * entire sidebar while logo/status/email still rendered.
 *
 * This console is a single product surface (Module 1 + Module 2). Any authenticated
 * session sees the nav; unauthenticated shells stay empty.
 */
export function resolveOfferingVisibility(user) {
  return { hasPlatform: Boolean(user) };
}
