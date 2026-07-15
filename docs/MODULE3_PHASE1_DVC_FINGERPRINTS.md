# Module 3 Phase 1 — DVC / SHA-256 Fingerprints

Spreadsheet Phase 1 called for **DVC + SHA-256 data integrity fingerprinting**.  
ZeroShield completes the **platform contract**; DVC (or any CI hash tool) remains **customer CI tooling**.

## What ZeroShield stores

On each model artifact (`POST /api/module3/llmops/artifacts/`):

| Field | Meaning |
|-------|---------|
| `data_sha256` | 64-hex SHA-256 of training/eval data snapshot (or DVC data hash exported as hex) |
| `model_sha256` | 64-hex SHA-256 of model weights / export used for Cosign blob payload |
| `signature_digest` | Cosign detached signature over `model_sha256` (see [MODULE3_COSIGN_PRODUCTION.md](./MODULE3_COSIGN_PRODUCTION.md)) |
| `image_ref` | Immutable container image reference |

Admission re-verifies Cosign against the stored fingerprints. Tampered data/model hashes fail cryptographic verify when the signed fingerprint no longer matches.

## How CI should compute fingerprints

### Option A — plain SHA-256 (minimum)

```bash
DATA_SHA=$(sha256sum data/train.parquet | awk '{print $1}')
MODEL_SHA=$(sha256sum model.safetensors | awk '{print $1}')
printf '%s' "$MODEL_SHA" > payload.txt
cosign sign-blob --yes --tlog-upload=false --key "$COSIGN_KEY" \
  --output-signature payload.sig payload.txt
```

### Option B — DVC-managed datasets

1. Track data/model with DVC (`dvc add …`, remote cache).
2. Export a **stable content hash** for registration:
   - Prefer hashing the **resolved artifact bytes** after `dvc pull` (same as Option A), or
   - Use your pipeline’s published digest if you already standardize on it.
3. Register the **64-hex** values (no `sha256:` prefix) on the artifact API.
4. Sign **exactly** the same `model_sha256` string Cosign will verify (no trailing newline).

ZeroShield does **not** host DVC remotes, remotes configs, or `dvc.lock` parsing. Poison detection is: *wrong hash or failed Cosign → deny + Module 2 incident*.

## Spreadsheet status

| Item | Status |
|------|--------|
| SHA-256 fingerprints on artifacts | Shipped |
| Cosign over model fingerprint | Shipped |
| DVC as CI fingerprint source | Documented contract (this page) |
| In-product DVC UI/remote | Out of scope / future |

## Related

- [MODULE3_COSIGN_PRODUCTION.md](./MODULE3_COSIGN_PRODUCTION.md)
- [MODULE3_PHASE1_E2E.md](./MODULE3_PHASE1_E2E.md)
- Sample CI: `.github/workflows/module3-cosign-admission-sample.yml`
