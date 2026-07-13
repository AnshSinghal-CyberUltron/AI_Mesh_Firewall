# CDL WASA Implementation Plan

Approved: 2026-07-07 | Branch: local only (no push)

## Fix Group A — Django pin (#2)

- File: `control/pyproject.toml` → `django>=6.0.5,<6.1`
- Verify: `manage.py check`, control pytest

## Fix Group B — Session (#1)

- File: `settings.py` → `REFRESH_TOKEN_LIFETIME` 7d → 1d
- Access token remains 60m with rotate + blacklist (unchanged)

## Fix Group C — Policy throttle (#5)

- `PolicyWriteThrottle` on create/update/partial_update/destroy
- Rate: `policy_write: 30/min` (env `POLICY_WRITE_THROTTLE_RATE`)

## Fix Group D — Schema/docs (#12)

- `ProtectedSpectacularAPIView` with `IsAuthenticated` + `IsAdminOrSuperuser`
- `docs_view` auth gate for HTML Scalar UI

## Fix Group E — Password (#13)

- `MinimumLengthValidator` OPTIONS min_length=12
- `PasswordComplexityValidator` (upper/lower/digit/special)
- Remove redundant 8-char check in change-password view

## Fix Group F — Regression tests

- `test_cdl_wasa_compliance.py` (control)
- `test_cdl_wasa_url_guard.py` (gateway admin db-test SSRF)

## Rollback

Revert pyproject pin, settings throttle/TTL, schema permissions, password validators.

## Verification

```bash
docker compose up -d
python scripts/ralph/cdl_wasa_verify.py
cd control && python -m pytest ai_mesh_control/policy/tests/test_cdl_wasa_compliance.py -q
cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_cdl_wasa_url_guard.py -q
```
