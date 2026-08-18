# Shared browser security headers. Include from http{} (proxy locations that
# do not set their own add_header inherit these) AND from every location that
# sets ANY add_header — nginx drops inherited add_header as soon as a block
# defines its own (SPA location / Cache-Control was dropping these on /login).
#
# CSP is frame-ancestors-only on purpose: a script-src policy would break the
# hashed Vite SPA. HSTS omits includeSubDomains so sibling hosts stay independent.
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "DENY" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Content-Security-Policy "frame-ancestors 'none'" always;
add_header Strict-Transport-Security "max-age=31536000" always;
