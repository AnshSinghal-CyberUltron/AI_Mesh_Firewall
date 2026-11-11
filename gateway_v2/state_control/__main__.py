"""`python -m state_control` — one re-hydrator process.

Deliberately not a verb on `gateway_v2.runtime.lifecycle`'s CLI: `state_control` is a separate
tree under the import-linter contract and the lint gates, and the control plane must be
deployable without the data plane.
"""

from state_control.service import main

if __name__ == "__main__":
    raise SystemExit(main())
