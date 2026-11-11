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

- **On every PR and push:** gitleaks and TruffleHog (verified) scan the new commits and block on any finding.
- **Every week:** the history scan alerts on anything not in `.gitleaksignore`. That file lists the historic fingerprints above, so the weekly scan does not re-alert on them. Being listed there does not make a value safe.
- **Locally:** `.gitignore` should exclude `.env*` except the `*.sample` files. Pair it with a pre-commit hook: `scripts/ralph/precommit-secret-scan.sh` already exists.
