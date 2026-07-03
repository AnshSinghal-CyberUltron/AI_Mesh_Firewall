#!/usr/bin/env python3
"""MCP-page cleanup item 16 — REDACTIONS=0 FIX (CLEANUP-15 diagnosis → CLEANUP-16 fix).

The §1.4 "sanitized" KPI reads `action_counts.redact` from the collapse=true
(frontend-default) threat-feed view. CLEANUP-15 proved redactions DO run but the collapse
aggregate built its block/redact rid-sets from the recent _THREAT_FEED_DEDUP_SCAN_CAP
window (4000 events); under heavy monitor volume the older redacts fell OUTSIDE that
window, so collapse=true reported redact=0.

CLEANUP-16 recomputes the collapse block/redact rid-sets AND the distinct-request total
from FULL DB queries. collapse=true is the ONE-ROW-PER-REQUEST view (CP31), so its KPI is
DISTINCT-REQUEST counts partitioned by strongest outcome (block > redact > monitor):
    block + redact + monitor == count(distinct requests).

This verifier proves the fix with an INDEPENDENT DB oracle (manage.py shell, org-scoped
exactly as the API scopes it) and asserts the live API collapse action_counts EQUALS it:
  1. redact > 0 when redactions exist (the headline fix; was 0)
  2. API collapse {block, redact, monitor, count} == DB distinct-request oracle (exact)
  3. per-request partition holds: block + redact + monitor == count
  4. NO block/redact event has a NULL request_id (else it would leak into monitor)
  5. distinct collapse counts <= raw non-collapse counts (collapse can't over-count)
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8100"
EMAIL = "admin@zeroshield.io"
PASS = "Adm1n!Pass#2024"
HOURS = 720
CONTROL_CTR = "ai_mesh_firewall-control-1"


def _req(method, path, headers, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"_raw": raw[:200]}


def login():
    st, b = _req("POST", "/api/auth/token/", {"Content-Type": "application/json"},
                 {"email": EMAIL, "password": PASS})
    if st != 200:
        raise SystemExit(f"login failed {st}: {b}")
    return b["access"]


def feed(tok, collapse):
    q = f"source=mcp_scan&limit=5&hours={HOURS}" + ("" if collapse else "&collapse=false")
    st, d = _req("GET", "/api/security/threat-feed/?" + q, {"Authorization": "Bearer " + tok})
    if st != 200:
        raise SystemExit(f"threat-feed collapse={collapse} failed {st}: {d}")
    ac = {k: int(v) for k, v in (d.get("action_counts") or {}).items()}
    return {"count": int(d.get("count") or 0), "ac": ac,
            "collapsed_by_request": d.get("collapsed_by_request")}


# INDEPENDENT ORACLE — recompute the distinct-request partition straight from the DB,
# org-scoped exactly the way get_request_organization() scopes the admin user, WITHOUT
# touching the view code under test.
_ORACLE = r'''
import json
from datetime import timedelta
from django.utils import timezone
from django.contrib.auth import get_user_model
from policy.models import EnforcementEvent
User = get_user_model()
u = User.objects.get(email="admin@zeroshield.io")
org = u.profile.organization
since = timezone.now() - timedelta(hours=%d)
qs = EnforcementEvent.objects.filter(created_at__gte=since, organization=org, metadata__source="mcp_scan")
def rids(action):
    return set(qs.filter(action=action).exclude(metadata__request_id__isnull=True).values_list("metadata__request_id", flat=True))
b, r = rids("block"), rids("redact")
redact_only = r - b
distinct_all = qs.exclude(metadata__request_id__isnull=True).values("metadata__request_id").distinct().count()
standalone = qs.filter(metadata__request_id__isnull=True).count()
total = distinct_all + standalone
null_block = qs.filter(action="block", metadata__request_id__isnull=True).count()
null_redact = qs.filter(action="redact", metadata__request_id__isnull=True).count()
out = {"block": len(b), "redact": len(redact_only), "monitor": max(0, total - len(b) - len(redact_only)),
       "count": total, "null_block": null_block, "null_redact": null_redact}
print("ORACLE_JSON=" + json.dumps(out))
''' % HOURS


def db_oracle():
    p = subprocess.run(
        ["docker", "exec", CONTROL_CTR, "python", "/app/control/manage.py", "shell", "-c", _ORACLE],
        capture_output=True, text=True, timeout=120)
    for line in p.stdout.splitlines():
        if line.startswith("ORACLE_JSON="):
            return json.loads(line[len("ORACLE_JSON="):])
    raise SystemExit(f"oracle produced no result:\nSTDOUT{p.stdout[-800:]}\nSTDERR{p.stderr[-800:]}")


def main():
    tok = login()
    col = feed(tok, collapse=True)
    non = feed(tok, collapse=False)
    orc = db_oracle()
    report = {"api_collapse": col, "api_noncollapse": non, "db_oracle": orc}

    c = col["ac"]
    c_block, c_redact = c.get("block", 0), c.get("redact", 0)
    c_monitor = c.get("monitor", 0) + c.get("allow", 0)
    n_redact = non["ac"].get("redact", 0)

    checks = {
        "collapse_is_collapsed": col["collapsed_by_request"] is True,
        # 1) headline fix — sanitized(redact) no longer 0 when redactions exist
        "redact_nonzero": (c_redact > 0) if orc["redact"] > 0 else True,
        # 2) API collapse EXACTLY equals the independent DB distinct-request oracle
        "api_matches_oracle_block": c_block == orc["block"],
        "api_matches_oracle_redact": c_redact == orc["redact"],
        "api_matches_oracle_monitor": c_monitor == orc["monitor"],
        "api_matches_oracle_count": col["count"] == orc["count"],
        # 3) per-request partition holds (no event lost)
        "partition_holds": (c_block + c_redact + c_monitor) == col["count"],
        # 4) no block/redact leaks into monitor via a NULL request_id
        "no_null_block_rid": orc["null_block"] == 0,
        "no_null_redact_rid": orc["null_redact"] == 0,
        # 5) distinct collapse counts can't exceed raw non-collapse counts
        "collapse_le_raw_count": col["count"] <= non["count"],
        "redact_le_raw": c_redact <= n_redact,
    }
    report["checks"] = checks
    report["item16Pass"] = all(checks.values())

    print(json.dumps(report, indent=2))
    print(f"ITEM-16: {'PASS' if report['item16Pass'] else 'FAIL'} — collapse KPI "
          f"sanitized(redact)={c_redact} denied(block)={c_block} approved(monitor)={c_monitor} "
          f"assembled(count)={col['count']}; oracle={{'block':{orc['block']},'redact':{orc['redact']},"
          f"'monitor':{orc['monitor']},'count':{orc['count']}}}; null_rids block={orc['null_block']} redact={orc['null_redact']}")
    sys.exit(0 if report["item16Pass"] else 1)


if __name__ == "__main__":
    main()
