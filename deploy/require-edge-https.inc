# Named HTTP vhosts only (not default_server). ALB TLS-terminates and forwards
# to :80 with X-Forwarded-Proto: https from a VPC address. Direct internet HTTP
# to the EC2 EIP (CWE-319) has a public $remote_addr and must not serve login
# or proxy /api/auth/ to Django.
#
# GET/HEAD → 301 HTTPS (no credentials on the request).
# POST/PUT/PATCH/DELETE/OPTIONS → 403 (do not 308: that would replay the body).
# $cleartext_https_redirect is mapped in nginx.conf (geo + proto). `if` +
# `return` is a documented-safe nginx pattern.
if ($cleartext_https_redirect = "https") {
    return 301 https://$host$request_uri;
}
if ($cleartext_https_redirect = "deny") {
    return 403;
}
