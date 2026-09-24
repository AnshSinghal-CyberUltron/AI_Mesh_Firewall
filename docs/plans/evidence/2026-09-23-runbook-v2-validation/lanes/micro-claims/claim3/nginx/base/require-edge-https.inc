# Named HTTP vhosts only (not default_server). ALB TLS-terminates and forwards
# to :80 with X-Forwarded-Proto: https from a VPC address. Direct internet HTTP
# to the EC2 EIP (CWE-319) has a public $remote_addr and must not serve login
# or proxy /api/auth/ to Django.
#
# GET/HEAD → 302 HTTPS (302 is not sticky-cached; a brief origin 301-to-self
# during a geo/proxy misconfig previously painted the SPA "Offline" forever).
# POST/PUT/PATCH/DELETE/OPTIONS → 403 (do not 308: that would replay the body).
# $cleartext_https_redirect is mapped in nginx.conf (geo + proto). `if` +
# `return` is a documented-safe nginx pattern.
if ($cleartext_https_redirect = "https") {
    return 302 https://$host$request_uri;
}
if ($cleartext_https_redirect = "deny") {
    return 403;
}
