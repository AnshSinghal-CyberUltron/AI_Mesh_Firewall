# Module 3 — Production Cosign Admission

## What this is

ZeroShield Module 3 can **deny untrusted AI model deployments** by verifying
Sigstore **Cosign** signatures before the workload is treated as admitted.

| Mode | Env | Behavior |
|------|-----|----------|
| Dev / demo | `MODULE3_ADMISSION_MODE=passthrough` (compose default) | Structural checks only; M3.1 Admission Simulator works |
| Production | `MODULE3_ADMISSION_MODE=verify` | Fail-closed Cosign CLI verify |

Gateway endpoint: `POST /v1/admin/admission/verify`  
Control UI/API: Module 3 → **M3.1 LLMOps** → register / verify artifact

**Shipped today:** detached blob verify (`cosign verify-blob`) and optional OCI image verify (`cosign verify`). Broader Sigstore ecosystem features (Rekor-only trust roots, keyless Fulcio identity policies, policy-controller) remain roadmap items — see [MODULE3_FUTURE_ENHANCEMENTS.md](./MODULE3_FUTURE_ENHANCEMENTS.md).

**Fingerprints / DVC:** CI supplies `data_sha256` + `model_sha256` (from `sha256sum` or DVC-resolved artifact bytes). See [MODULE3_PHASE1_DVC_FINGERPRINTS.md](./MODULE3_PHASE1_DVC_FINGERPRINTS.md).

**Sample GitHub Actions:** [module3-cosign-admission-sample.yml](../.github/workflows/module3-cosign-admission-sample.yml)

---

## Configure production (recommended compose overlay)

Default compose stays on **passthrough** so demos keep working. Enable Cosign with:

```bash
# 1) Dev keypair (writes secrets/cosign.key + secrets/cosign.pub — never commit the private key)
bash scripts/module3_gen_cosign_keypair.sh

# 2) Shared control↔gateway admin secret in .env
# GATEWAY_INTERNAL_API_KEY=<long-random>

# 3) Gateway in verify mode + pubkey mount (secrets/ is already mounted at /run/secrets)
docker compose -f docker-compose.yml -f docker-compose.module3-verify.yml up -d gateway

# 4) Workers required so deny → Module 2 incidents are processed
docker compose --profile workers up -d
# (or whichever workers profile your deployment uses)
```

`.env` equivalents (if you prefer setting verify in `.env` instead of the overlay):

```env
MODULE3_ADMISSION_MODE=verify
MODULE3_COSIGN_PUBLIC_KEY_FILE=/run/secrets/cosign.pub
MODULE3_COSIGN_BINARY=cosign
MODULE3_COSIGN_TIMEOUT_SEC=45
GATEWAY_INTERNAL_API_KEY=<same-value-on-control-and-gateway>
```

Or inline PEM (less preferred):

```env
MODULE3_COSIGN_PUBLIC_KEY="-----BEGIN PUBLIC KEY-----
...
-----END PUBLIC KEY-----"
```

Aliases: `MODULE3_COSIGN_PUBLIC_KEY_PATH` and `MODULE3_COSIGN_PUBLIC_KEY_FILE` are both accepted.

Rebuild gateway so Cosign CLI is installed:

```bash
docker compose build gateway
docker compose -f docker-compose.yml -f docker-compose.module3-verify.yml up -d gateway
```

---

## Live e2e harness

```bash
# Preferred (no host Cosign/curl required) — runs inside gateway image:
docker compose -f docker-compose.yml -f docker-compose.module3-verify.yml run --rm --no-deps \
  -v "$PWD/scripts:/scripts:ro" \
  -e CONTROL_URL=http://control:8000 \
  -e COSIGN_KEY=/run/secrets/cosign.key \
  -e TEST_EMAIL=admin@zeroshield.io \
  -e TEST_PASSWORD='…' \
  -e REQUIRE_VERIFY=1 \
  --entrypoint python3 gateway /scripts/module3_admission_e2e.py
```

Host shell variant (needs Cosign + curl on PATH):

```bash
bash scripts/module3_admission_e2e.sh
```

This signs a fingerprint with Cosign, registers the artifact, asserts **allow** under `verify` mode, asserts **deny** for a bad signature, then polls Module 2 until the admission-denied incident appears.

Optional short example (prints payload fields; with `POST_TO_CONTROL=1` also registers/verifies):

```bash
bash scripts/module3_cosign_sign_example.sh
POST_TO_CONTROL=1 bash scripts/module3_cosign_sign_example.sh
```

Full Phase 1 + both-pages command cheat sheet: [MODULE3_PHASE1_E2E.md](./MODULE3_PHASE1_E2E.md).

---

## Two verify styles

### A) Detached blob (default) — `MODULE3_COSIGN_VERIFY_IMAGE=0`

CI signs the model fingerprint string (64 hex chars, **no** `sha256:` prefix, exact bytes):

```bash
MODEL_SHA=$(sha256sum model.safetensors | awk '{print $1}')
printf '%s' "$MODEL_SHA" > payload.txt
cosign sign-blob --key cosign.key --output-signature payload.sig payload.txt

# Store in ZeroShield artifact:
#   model_sha256 = $MODEL_SHA
#   signature_digest = base64(payload.sig)   # or cosign-blob:<base64>
#   image_ref = your container image (still required by API)
```

Admission runs:

`cosign verify-blob --key <pub> --signature <sig> <payload>`

The gateway normalizes `model_sha256` / `data_sha256` to lowercase and strips a `sha256:` prefix before writing the payload file. CI must sign that same 64-hex fingerprint (prefer lowercase).

### B) OCI image signatures — `MODULE3_COSIGN_VERIFY_IMAGE=1`

```bash
cosign sign --key cosign.key registry.example.com/org/model:1.2.0
# admission:
cosign verify --key cosign.pub registry.example.com/org/model:1.2.0
```

Gateway must reach the registry. Optional:

```env
MODULE3_COSIGN_EXTRA_ARGS=--insecure-ignore-tlog
```

(use only when you understand transparency-log implications)

---

## Client CI sketch

```text
build image / export weights
  → compute model_sha256
  → cosign sign-blob (or cosign sign image)
  → POST /api/module3/llmops/artifacts/   (register)
  → POST /api/module3/llmops/verify/      (admission)
  → if allow → kubectl / helm deploy
  → if deny → stop pipeline (Module 2 incident may open)
```

---

## Local development

```env
MODULE3_ADMISSION_MODE=passthrough
```

Use the UI **Admission Simulator** on M3.1 (passthrough digests only). Do **not** enable `verify` for demos unless you have a real key + signatures.

---

## Fail-closed behavior

In `verify` mode ZeroShield **denies** when:

- Public key env is missing / unreadable
- Cosign binary is missing
- Cosign exits non-zero
- Cosign times out
- `signature_digest` is `invalid` / `invalid:…` (explicit deny)
- SHA fingerprints are present but malformed

---

## Related files

- `gateway/ai_mesh_gateway/admission_controller.py`
- `gateway/ai_mesh_gateway/cosign_verify.py`
- `gateway/Dockerfile` (installs Cosign CLI)
- `docker-compose.module3-verify.yml`
- `scripts/module3_gen_cosign_keypair.sh`
- `scripts/module3_cosign_sign_example.sh`
- `scripts/module3_admission_e2e.sh`
- `control/ai_mesh_control/module3/adapters/gateway_admission.py`
