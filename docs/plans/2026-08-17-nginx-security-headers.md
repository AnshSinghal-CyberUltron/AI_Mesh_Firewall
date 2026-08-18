# Nginx Security Headers + Host Isolation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close pentest findings #7 (missing security headers), #8 (clickjacking / unknown-Host serves the SPA), #11 (credentials over HTTP — missing HSTS on `/login`), and #1 (nginx version disclosure) on `aimeshfirewall.zeroshield.ai`.

**Architecture:** Production UI is served by the baked `ai-mesh-nginx` image (`deploy/Dockerfile.nginx` copies `deploy/nginx.conf` → `/etc/nginx/conf.d/01-http.conf` and `deploy/nginx-ssl.conf` → `02-ssl.conf`). An AWS ALB terminates public HTTP (301 → HTTPS) and forwards HTTPS to origin nginx :80. Origin :443 exists for Cloudflare Full / direct TLS. Security headers are declared at the http{} level, but nginx **drops inherited `add_header` as soon as a location sets any `add_header`**. The SPA `location /` sets only `Cache-Control`, so `/login` ships with **zero** CSP/HSTS/XFO/XCTO. Unknown Host on :443 hits the first SSL server (the UI).

**Tech Stack:** nginx 1.27 alpine, Docker-baked config, ALB in front of EC2, Django control behind `/api/`.

---

## Discovered architecture (evidence)

| Layer | Behavior | Evidence |
|-------|----------|----------|
| Public HTTP | ALB `awselb/2.0` 301 → `https://host:443/...` | `curl -sI http://aimeshfirewall.zeroshield.ai/login` |
| Public HTTPS `/login` | 200 HTML, `Cache-Control: no-store`, **no** CSP/HSTS/XFO/XCTO, `Server: nginx/1.27.5` | live `curl -sI https://aimeshfirewall.zeroshield.ai/login` |
| Public HTTPS `/api/auth/token/` | 401 JSON **with** all security headers | `/api/` has no location `add_header`, so http-level headers apply |
| HTTPS via EC2 hostname / bogus Host | 200 SPA (implicit default SSL vhost = UI) | `curl -skI https://ec2-15-252-51-165.ap-south-1.compute.amazonaws.com/login` |
| Origin HTTP vhost `location /` | `add_header Cache-Control` only → **strips** http-level security headers | `deploy/nginx.conf` |
| `server_tokens` | unset (image default on) | live `Server: nginx/1.27.5` |
| Django | `SECURE_SSL_REDIRECT` default **false**; cookies Secure when DEBUG=false | `control/.../settings.py` |
| Deploy health | `curl -H "Host: $FRONTEND_HOST" http://127.0.0.1/` | `scripts/deploy-ec2.sh` — named vhost, not default_server |

## Current behavior vs findings

1. **#7 Missing headers (CWE-1021/693)** — TRUE on `/login` and `/`. FALSE on `/api/*`. Root cause is add_header inheritance, not "headers never configured".
2. **#8 Clickjacking (CWE-1021)** — TRUE: `/login` has no `X-Frame-Options` / `CSP frame-ancestors`. Description also mixes Host-header / IP access: TRUE on :443 unknown Host (SPA served). Public :80 unknown Host is ALB 301, not nginx.
3. **#11 Credentials plaintext (CWE-319)** — Public HTTP POST to `/api/auth/token/` is ALB 301 (body may still be sent on the HTTP hop to the ALB). Origin `/login` **lacks HSTS**, so browsers are not instructed to stick to HTTPS. Do **not** 301 HTTP→HTTPS on origin :80 — ALB already terminated TLS and forwards HTTP to nginx with `X-Forwarded-Proto: https`; an origin-side HTTPS redirect would break or loop.
4. **#1 Server version (CWE-200)** — TRUE: `Server: nginx/1.27.5`.

## Suspected issues (ranked)

| Rank | Hypothesis | Confidence | Verification |
|------|------------|------------|--------------|
| 1 | SPA `location /` Cache-Control `add_header` suppresses http-level security headers | **High** | live `/login` vs `/api/` header split; nginx docs |
| 2 | `server_tokens` never set | **High** | live Server header |
| 3 | No SSL `default_server`; first 443 vhost is the UI | **High** | HTTPS EC2 hostname / bogus Host → 200 SPA |
| 4 | HTTP default_server currently **serves the UI** (comment: "direct IP / unknown Host → firewall UI") | **High** | `deploy/nginx.conf` lines 226–258 |
| 5 | Scanner POST over HTTP to ALB before 301 | **Med** | live POST HTTP → 301; HSTS on `/login` is the origin-side control |

## Proposed fixes

1. **`server_tokens off;`** at http level in `deploy/nginx.conf` (conf.d is inside http{}).
2. **`deploy/security-headers.inc`** — single snippet:
   - `X-Content-Type-Options: nosniff`
   - `X-Frame-Options: DENY`
   - `Referrer-Policy: strict-origin-when-cross-origin`
   - `Content-Security-Policy: frame-ancestors 'none'; upgrade-insecure-requests`
   - `Strict-Transport-Security: max-age=31536000` (no includeSubDomains — sibling hosts stay independent)
3. Keep http-level `include` of that snippet (or equivalent add_header) for proxy locations that do not set their own headers.
4. **Every location that already has `add_header`** (SPA `/`, `/assets/`, edge-error JSON) must **re-include** the snippet in the same block. Especially SPA `location /`.
5. **nginx-ssl.conf `location /`**: add Cache-Control no-store **and** security-headers.inc (parity with HTTP; today SSL `/` has neither Cache-Control nor explicit headers).
6. **Unknown Host isolation (finding #8 recommendation)** without killing ALB health checks:
   - HTTP + HTTPS `default_server` / `server_name _`
   - `location = /` and `location = /healthz` → `200 text/plain ok` (ALB often probes `/` with Host=IP)
   - all other URIs (including `/login`, `/api/auth/token/`) → **403**
   - do **not** `try_files` the SPA on default_server
7. COPY `security-headers.inc` in `deploy/Dockerfile.nginx`.
8. **Do not** enable origin HTTP→HTTPS redirect on :80 (ALB TLS termination).
9. **Do not** set Django `SECURE_SSL_REDIRECT=true` in this change (would 301 based on `X-Forwarded-Proto`; if a hop omits the header, login breaks). HSTS + ALB 301 + CSP upgrade-insecure-requests are the controls.
10. Regression gate: extend `scripts/test_docker_compose_prod.sh` (or a small `scripts/test_nginx_security.sh`) to fail if `server_tokens off` missing, if any SPA `location /` has Cache-Control without security-headers.inc, or if default_server still `try_files` `/index.html`. Optional: `docker run nginx:1.27-alpine` + `nginx -t` + curl against a temp container.

## Risks

- **ALB health check Host=IP path=/** — mitigated by default_server `location = /` returning 200 (not the SPA).
- **CSP `upgrade-insecure-requests`** — browsers upgrade http:// subresources on this origin; intended. Mixed-content third-party http URLs on the login page would be upgraded (login page is a tiny SPA shell).
- **HSTS on HTTP origin responses** — ALB forwards HTTPS as HTTP to nginx; nginx still emits HSTS on those responses; browser sees HTTPS from ALB. Fine. Emitting HSTS on a true cleartext response is ignored by browsers; harmless.
- **444 vs 403** — 403 keeps ALB/logs observable; 444 would look like a timeout to some scanners/LBs. Prefer 403.
- Production does not change until `ai-mesh-nginx` is rebuilt and `nginx` recreated on EC2.

## Dependencies

- Rebuild nginx image to bake config (`infra/scripts/build-push-images.sh` / `make ecr-push`) then `scripts/deploy-ec2.sh` nginx recreate. Out of scope unless asked.

---

### Task 1: Failing regression gate

**Files:** Create `scripts/test_nginx_security.sh`; Modify `scripts/verify_prod_local.sh` (call it); Modify `scripts/test_docker_compose_prod.sh` OR keep as sibling invoked from verify.

**Step 1:** Write a bash gate that asserts:
- `grep -q 'server_tokens off;' deploy/nginx.conf`
- every `location /` that contains `Cache-Control` also contains `security-headers.inc` or `X-Frame-Options`
- default_server blocks do not `try_files` `/index.html`
- Dockerfile.nginx COPYs `security-headers.inc`
- `nginx-ssl.conf` has `listen 443 ssl default_server` (or equivalent default_server)

**Step 2:** Run it; expect FAIL on current tree.

**Step 3:** Implement config until the gate passes.

**Step 4:** `nginx -t` via `docker run --rm -v ... nginx:1.27-alpine nginx -t`.

---

### Task 2: Shared header snippet + server_tokens

**Files:** Create `deploy/security-headers.inc`; Modify `deploy/nginx.conf`; Modify `deploy/nginx-ssl.conf`; Modify `deploy/edge-error.inc`; Modify `deploy/Dockerfile.nginx`.

---

### Task 3: SPA locations include snippet

Repeat security headers next to Cache-Control on HTTP `/`, HTTP `/assets/`, SSL `/`, SSL `/assets/`, default_server replacements.

---

### Task 4: default_server Host isolation

HTTP + HTTPS default_server: `/` and `/healthz` 200 plain; else 403. Include security headers on those responses.

---

### Task 5: Live / local proof

- Temp nginx container: Host `aimeshfirewall.zeroshield.ai` `/login` has CSP, XFO, HSTS, XCTO; `Server` has no version.
- Host `_` or IP `/login` → 403; `/` → 200 ok.
- After deploy (if performed): `curl -sI https://aimeshfirewall.zeroshield.ai/login` shows headers; `Server:` without `/1.27.5`.
