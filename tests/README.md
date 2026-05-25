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
