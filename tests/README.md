# tests

| Directory | Purpose |
|-----------|---------|
| `unit/` | Fast tests per package |
| `integration/` | compose-backed API flows |
| `load/` | Locust / k6 for 10k→100k gates |
| `security/` | Policy signing, auth, tenant isolation |

Run from repo root after extraction:

```bash
pytest tests/unit -q
```

## Backend integration tests (Postgres)

Module 2 backend tests require Postgres-backed migrations (they fail on SQLite due to Postgres-specific migration SQL).

From repo root on Windows/PowerShell:

```powershell
.\scripts\run-control-tests-postgres.ps1 module2.tests
```

Run a single test label:

```powershell
.\scripts\run-control-tests-postgres.ps1 module2.tests.test_module2_pages_api.Module2PagesApiTests.test_threat_intel_write_requires_admin_or_superuser
```
