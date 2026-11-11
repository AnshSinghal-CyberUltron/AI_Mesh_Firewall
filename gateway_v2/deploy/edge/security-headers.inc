# V2 edge (gateway_v2/deploy/edge/) copy of the shipped v1 security-header set,
# reused per GW16b R4.2. This is its OWN copy of the v1 directives (deploy/security-headers.inc),
# NOT a cross-tree include of deploy/ — the v2 edge tree is self-contained.
#
# Include from the named server (and from every location that sets ANY per-response header —
# nginx drops an inherited header as soon as a block defines its own).
#
# CSP is frame-ancestors-only on purpose: a script-src policy would break a hashed Vite SPA.
# HSTS omits includeSubDomains so sibling hosts stay independent.
# MUST NOT emit the CSP directive that forces plaintext upgrades (the v1 security gate forbids it).
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "DENY" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Content-Security-Policy "frame-ancestors 'none'" always;
add_header Strict-Transport-Security "max-age=31536000" always;
