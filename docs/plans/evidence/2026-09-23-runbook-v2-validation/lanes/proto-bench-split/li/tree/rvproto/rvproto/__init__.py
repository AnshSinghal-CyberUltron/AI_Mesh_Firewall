"""rvproto: throwaway v2 critical-path prototype (runbook §10) for measurement only.

Bounds come from the REAL GW03 ResourceContract in gateway_v2.runtime, imported read-only from
RV_GATEWAY_V2_PATH, else a vendored copy next to this checkout (units: ~/rv/vendor), else the
repo checkout on the controller.
"""

import os
import sys
from pathlib import Path

_CANDIDATES = (
    os.environ.get("RV_GATEWAY_V2_PATH", ""),
    str(Path(__file__).resolve().parents[2] / "vendor"),
    "/home/contact_cyberultron_com/AI_Mesh_Firewall/gateway_v2",
)
for _p in _CANDIDATES:
    if _p and (Path(_p) / "gateway_v2" / "runtime" / "resources.py").is_file():
        if _p not in sys.path:
            sys.path.insert(0, _p)
        break
