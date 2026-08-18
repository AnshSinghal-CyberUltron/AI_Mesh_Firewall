# A-14 / A-34 (2026-07-31): JSON edge-error page. Included inside every server{} block so an
# nginx-GENERATED 4xx (malformed request line / URI traversal / duplicate Authorization / oversized
# body / missing static asset) returns a JSON envelope + an X-Request-ID header instead of nginx's
# stock HTML. Upstream (proxied) 4xx are UNAFFECTED — proxy_intercept_errors is off by default, so
# the application's own error bodies pass through untouched.
#
# Why a STATIC FILE and not a named location (`@json_error`): the very-early request-line 400
# (e.g. `..%2f..%2f` traversal) leaves the URI empty, and error_page -> a NAMED location then fails
# with "empty URI in redirect to named location" -> turns the 400 into a 500 (a regression). An
# error_page pointing at a concrete internal URI survives it — verified: `..%2f` stays 400, now JSON.
# No `=` on error_page: the ORIGINAL status is preserved (403 stays 403, 404 stays 404).
#
# The 5 security add_headers are included here because nginx does not inherit
# http-level add_header into a block that defines its own (X-Request-ID) —
# omitting them would drop them from error bodies.
error_page 400 401 403 404 405 413 414 431 /__edge_error.json;
location = /__edge_error.json {
    internal;
    root /etc/nginx/edge_errors;
    default_type application/json;
    include /etc/nginx/security-headers.inc;
    add_header X-Request-ID $request_id always;
}
