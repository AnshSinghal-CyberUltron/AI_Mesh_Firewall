# Release checklist (post-deployment / staging)

Use this after merging RAG, Module 2, demo, or gateway changes. **Do not deploy from a dirty workspace without committing.**

---

## 1. Pre-release

- [ ] All intended changes **committed** and pushed to the release branch
- [ ] Confirm **`docker-compose.override.yml` is NOT used in production** (it sets `SSL_VERIFY=false` and `VECTOR_PROVIDER_ALLOW_PRIVATE=1` for local dev only)
- [ ] `.env` on the target host has real secrets (not dev defaults for `DJANGO_SECRET_KEY`, API keys, etc.)

---

## 2. Build and deploy images

From repo root:

```powershell
docker compose build gateway control frontend workers
docker compose up -d gateway control frontend workers redis postgres
```

For RAG-enabled environments:

```powershell
docker compose --profile chroma up -d chromadb
```

Rebuild is required when these paths change:

| Service | Paths |
|---------|--------|
| **gateway** | `gateway/ai_mesh_gateway/**` |
| **control** | `control/ai_mesh_control/**` |
| **frontend** | `frontend/src/**` |

Demo (`:8765`) runs **outside** Docker by default — restart separately after demo code changes.

---

## 3. One-time RAG bootstrap (per environment)

```powershell
cd demo\zeroshield-openai-demo
python scripts\bootstrap_rag.py
```

Expect: vector policy for `demo_knowledge` exists/created, Chroma provider URL `http://chromadb:8000`, policies compiled to Redis.

---

## 4. Automated staging verification

```powershell
# Full RAG E2E (Chroma check, bootstrap, readiness, ingest, Zedland query)
python scripts\verify_rag_staging.py

# Skip bootstrap if already applied
python scripts\verify_rag_staging.py --skip-bootstrap
```

Demo must be running:

```powershell
cd demo\zeroshield-openai-demo
python -m app.server
```

---

## 5. Backend test gates

```powershell
# Gateway
cd gateway
.\.venv\Scripts\python -m pytest ai_mesh_gateway\tests -q

# Control Module 2 analytics
cd control
python -m pytest ai_mesh_control\module2\tests\test_lane_expansion.py ai_mesh_control\module2\tests\test_analytics.py -q

# Demo contract tests (fast)
cd demo\zeroshield-openai-demo
python -m pytest tests\test_rag_pipeline.py tests\test_status_reason.py tests\test_sdk_scenarios.py -q
```

---

## 6. Frontend / UI gates

```powershell
cd frontend
npm run lint
npm run build
```

Playwright (live stack on `:8180`):

```powershell
$env:NODE_PATH="$PWD\tests\e2e\node_modules"
$env:BASE_URL="http://127.0.0.1:8180"
node scripts\playwright_m23_model_rag_sync.mjs
```

Login: `admin@zeroshield.io` / `Adm1n!Pass#2024`

---

## 7. Manual smoke (customer perspective)

### Demo RAG (`http://127.0.0.1:8765`)

1. Hard-refresh (`Ctrl+Shift+R`) — cache bust `app.js?v=14`
2. **Index** Zedland doc → 200 / accepted
3. **Query + Synthesize** → answer mentions **Zedopolis**
4. Pipeline sidebar → **Query Scan**, **Vector Retrieval** (not all chat N/A)

### Module 2 (`http://127.0.0.1:8180`)

1. Login → **Model & RAG** tab
2. After demo RAG traffic, stage KPIs and `demo_knowledge` collection exposure update within ~1–2 min (telemetry drain)

---

## 8. Production risks to watch

| Signal | Action |
|--------|--------|
| `rag_pipeline_blocked` / vector errors | Chroma up + `bootstrap_rag.py` |
| Redis timeout in gateway logs | Fix Redis before trusting Module 2 dashboards |
| Demo shows raw JSON / wrong pipeline | Restart demo + hard-refresh browser |
| RAG KPIs inflated | Ensure control image includes analytics dedup fix (`ingest_events` separate from query stage) |

---

## 9. Rollback

```powershell
docker compose pull   # previous tagged images, if tagged
docker compose up -d gateway control frontend
```

Keep previous image tags in your registry for fast rollback.
