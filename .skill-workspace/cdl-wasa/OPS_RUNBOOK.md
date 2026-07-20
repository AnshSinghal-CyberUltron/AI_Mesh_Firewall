# CDL WASA Production Ops Runbook

Scope: `https://aisecshield.zeroshield.ai/` (and sibling `aimeshfirewall.zeroshield.ai`)

These items cannot be fully remediated in application code alone. Track in change management and verify after deploy.

## #3 — nginx 1.24 EOL (Medium)

- Upgrade origin/nginx image to a supported stable release (1.26+ or vendor LTS).
- Confirm `server_tokens off;` and that `Server` header does not leak version.
- Apply headers from `deploy/nginx.conf`:
  - `Strict-Transport-Security`
  - `X-Frame-Options: SAMEORIGIN`
  - `Content-Security-Policy` with `frame-ancestors 'self'`
  - `X-Content-Type-Options: nosniff`
  - `Referrer-Policy: strict-origin-when-cross-origin`

## #4, #15, #16 — OpenSSH (Low)

- Upgrade OpenSSH to ≥ 9.6 (Terrapin mitigation).
- `/etc/ssh/sshd_config`:
  - `PasswordAuthentication no`
  - `PermitRootLogin no`
  - Restrict `Ciphers` to `chacha20-poly1305@openssh.com,aes256-gcm@openssh.com`
  - Restrict `MACs` to `hmac-sha2-256-etm@openssh.com,hmac-sha2-512-etm@openssh.com`
  - Enable `strict-kex` where supported

## #14 — TLS cipher policy (Low)

- At ALB/Cloudflare/origin: TLS 1.2+ only; prefer AES-256-GCM.
- Disable weak CBC/RC4/3DES suites.
- Run `testssl.sh` or SSL Labs scan after change.

## #17 — Certificate lifecycle (Info)

- Ensure ACME auto-renew (Let's Encrypt or vendor).
- Alert at T-30 days on cert expiry.
- Document owner and rollback for cert rotation.

## #7, #8, #11 — Edge HTTPS + headers (Low)

- Force HTTPS redirect at CDN/origin (`SECURE_SSL_REDIRECT` is enabled when `DEBUG=False` in Django).
- HSTS `max-age` ≥ 31536000 with `includeSubDomains` at edge.
- Verify clickjacking: `curl -sI https://aisecshield.zeroshield.ai/ | grep -i frame`

## Verification commands (production)

```bash
curl -sI https://aisecshield.zeroshield.ai/
ssh -V
nginx -v
echo | openssl s_client -connect aisecshield.zeroshield.ai:443 -servername aisecshield.zeroshield.ai 2>/dev/null | openssl x509 -noout -dates
```

## Local stack note

Vite dev server (`:8180`) may not emit production nginx headers. Use this runbook for production sign-off; local app-layer fixes are gated by `scripts/ralph/cdl_wasa_verify.py`.
