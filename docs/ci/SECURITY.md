# Secrets in git history: status and required actions

**Finding (11 Nov 2026, full-history gitleaks scan).** This repository is public, and its history contains credential-shaped values committed in June 2026. Deleting a file does not remove it from history.

| Commit(s) | File | Kinds of value (secrets not reproduced here) |
|---|---|---|
| `40bacebf`, `e9e81554`, `00e81b1f` | `.env` | AWS access key ID and secret, Azure AD client secret, `POLICY_SIGNING_KEY`, `GATEWAY_INTERNAL_API_KEY`, `METRICS_SCRAPER_KEY` |
| `40bacebf` (deleted in `7ed44575`) | `.env.bak` | AWS access key ID and secret |
| `40bacebf` | `infra/terraform/envs/prod/prod.tfvars` | `db_password`, `django_secret_key`, `policy_signing_key`, `field_encryption_key`, `gateway_internal_api_key` |
| `6bf509d1` | `docker-compose.cosign-e2e.yml` | `GATEWAY_INTERNAL_API_KEY` |

## Owner decision (11 Nov 2026)

The owner reviewed this finding and decided:

- The values are development credentials with limited budget and access.
- They will be rotated after the current build phase.
- The repository will be made private at that point.

Until then, development continues with these values. The `.gitleaksignore` baseline stays in place, so CI blocks only **new** findings. The rotation table below stays open until the rotation is done.

## Required actions (owner)

1. **Rotate every value above.** Treat each as compromised whether or not it is still in use:
   - Revoke the AWS keys in IAM.
   - Rotate the Azure app secret.
   - Change the production database password and the Django secret key.
   - Rotate the policy signing, internal API and metrics keys.
   - Plan a re-encryption migration for `field_encryption_key`, because stored ciphertext depends on it.
2. **Optional: rewrite history** with `git filter-repo` or BFG. This rewrites every commit hash on every branch, so every clone and open PR must be re-created. Rotation, not history rewriting, is what removes the risk.
3. After rotating, record the date in the table below.

| Value | Rotated on | By |
|---|---|---|
| AWS keys (`.env`, `.env.bak`) | | |
| Azure AD client secret | | |
| Production DB password | | |
| Django secret key | | |
| Policy signing key | | |
| Field encryption key (+ re-encryption) | | |
| Gateway internal API key | | |
| Metrics scraper key | | |

## How CI prevents a recurrence

- **On every PR and push:** gitleaks and TruffleHog (verified only) scan the new commits and block on any finding.
  - **Merge commits are covered.** `git log -p` shows no diff for a merge, so a secret added while resolving a merge would otherwise pass. gitleaks runs with `--remerge-diff`, which shows exactly what each merge commit itself introduced. TruffleHog has no equivalent option; gitleaks is the merge-commit gate.
  - **Test paths are not a blind spot.** Every rule is skipped only in the detector test corpora (`gateway/ai_mesh_gateway/tests/`, `gateway/tests/`, `gateway_v2/tests/`, `tests/detection_corpus/`) and in specs and recorded evidence. In any other `tests/` or `fixtures/` path only the noisy `generic-api-key` rule is skipped, so a real-format AWS key, private key or token there is still caught.
  - **A scan that errors fails.** TruffleHog runs with `--fail-on-scan-errors` on a change range, so an aborted scan cannot report "0 found". gitleaks fails on any `[git] error` line and, on a change range, when it scanned fewer commits than the range holds.
  - **Accepted trade-off:** TruffleHog reports only *verified* results. A live key whose provider cannot be reached at scan time ("unknown") does not block through TruffleHog; gitleaks still blocks on the pattern.
- **Every week:** the full-history sweep (all refs, merge commits included).
  - **gitleaks blocks** on anything not in `.gitleaksignore`. That file lists the historic fingerprints above, so the sweep does not re-alert on them. Being listed there does not make a value safe.
  - **TruffleHog reports** (a warning annotation per verified credential): history cannot be fixed by the run that finds it.
- **Reports never carry a secret value.** gitleaks runs with `--redact`. TruffleHog's JSON carries each value, so its raw output stays in the runner's temp directory and the uploaded artifact keeps only detector, file, commit and line.
- **Locally:** `.gitignore` should exclude `.env*` except the `*.sample` files. Pair it with a pre-commit hook: `scripts/ralph/precommit-secret-scan.sh` already exists.
