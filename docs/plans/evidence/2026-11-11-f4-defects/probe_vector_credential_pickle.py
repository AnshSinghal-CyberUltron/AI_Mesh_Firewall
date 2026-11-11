"""R17 probe: does OrgCredential (dispatch/vector_credentials.py) put its secret in pickle bytes?

Run from gateway_v2/ with any interpreter: <python> -I <evidence>/probe_vector_credential_pickle.py
"""
import copy
import pickle
import sys

sys.path.insert(0, ".")
from gateway_v2.dispatch.vector_credentials import OrgCredential  # noqa: E402

SECRET = "PLANTED-SECRET-123"
cred = OrgCredential(org_id="o", verified=True, _secret=SECRET, ref_id="r1")
out: dict[str, object] = {"python": sys.version.split()[0]}
for proto in range(pickle.HIGHEST_PROTOCOL + 1):
    try:
        raw = pickle.dumps(cred, protocol=proto)
        out[f"dumps_p{proto}_secret_in_bytes"] = SECRET.encode() in raw
    except Exception as exc:  # noqa: BLE001
        out[f"dumps_p{proto}"] = type(exc).__name__
for name, fn in (("loads", lambda: pickle.loads(pickle.dumps(cred))),
                 ("deepcopy", lambda: copy.deepcopy(cred))):
    try:
        out[name] = "ok" if fn()._secret == SECRET else "ok-but-different"
    except Exception as exc:  # noqa: BLE001
        out[name] = type(exc).__name__
out["repr_redacted"] = SECRET not in repr(cred)
print(out)
