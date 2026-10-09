"""`python -m audit_control` — the audit exporter process.

Separate from `service.py` so importing the service to test it does not run it.
"""

from __future__ import annotations

from audit_control.service import main

if __name__ == "__main__":
    raise SystemExit(main())
