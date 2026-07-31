#!/usr/bin/env python3
"""M2.6 Incident Queue & Forensics — Steps 1–4 API verification."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import quote

BASE = os.environ.get("BASE_URL", os.environ.get("CONTROL_URL", "http://127.0.0.1:8100")).rstrip("/")
EMAIL = os.environ.get("TEST_EMAIL", "admin@zeroshield.io")
PASSWORD = os.environ.get("TEST_PASSWORD", "Adm1n!Pass#2024")
ORG_SLUG = os.environ.get("ORG_SLUG", "zeroshield")
CONTROL_CONTAINER = os.environ.get("CONTROL_CONTAINER", "ai_mesh_firewall-control-1")
STAMP = os.environ.get("M26_PROBE_STAMP", datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"))
PROBE_TITLE = os.environ.get("M26_PROBE_TITLE", f"M2.6 E2E probe {STAMP}")
ALERT_RULE_NAME = os.environ.get("M26_ALERT_RULE_NAME", f"M2.6 alert E2E {STAMP}")
LIST_URL = f"{BASE}/api/module2/incidents/"


def request(method: str, url: str, *, headers: dict | None = None, body: dict | None = None):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return exc.code, payload


def summary_snapshot(payload: dict) -> dict:
    s = payload.get("summary") or {}
    return {
        "total": int(s.get("total") or 0),
        "active": int(s.get("active") or 0),
        "open": int(s.get("open") or 0),
        "investigating": int(s.get("investigating") or 0),
        "escalated": int(s.get("escalated") or 0),
        "resolved": int(s.get("resolved") or 0),
        "critical_high": int(s.get("critical_high") or 0),
    }


def docker_shell(py: str) -> str:
    proc = subprocess.run(
        [
            "docker",
            "exec",
            "-w",
            "/app/control",
            CONTROL_CONTAINER,
            "python",
            "manage.py",
            "shell",
            "-c",
            py,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "docker shell failed")
    return proc.stdout or ""


def parse_last_int(stdout: str) -> int:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.isdigit():
            return int(line)
    raise RuntimeError(f"id not found in output: {stdout!r}")


def create_probe_incident(title: str) -> int:
    py = (
        "from auth.models import Organization; "
        "from policy.models import EnforcementEvent, SecurityIncident; "
        "from policy.constants import ACTION_BLOCK; "
        f"title={title!r}; "
        f"org=Organization.objects.filter(slug={ORG_SLUG!r}).first(); "
        "assert org; "
        "ev=EnforcementEvent.objects.create("
        "organization=org, action=ACTION_BLOCK, metadata={"
        "'source':'threat_intel','threat_type':'prompt_injection',"
        "'detail':title,'model':'gpt-4o','key_prefix':'zs_m26'"
        "}); "
        "inc=SecurityIncident.objects.create("
        "organization=org, enforcement_event=ev, title=title, "
        "severity='high', status='open'"
        "); print(inc.id)"
    )
    return parse_last_int(docker_shell(py))


def fire_alert_rule_incident(rule_name: str) -> int:
    py = (
        "from auth.models import Organization; "
        "from module2.models import AlertRule; "
        "from module2.tasks import evaluate_alert_rules; "
        "from policy.constants import ACTION_BLOCK; "
        "from policy.models import EnforcementEvent, SecurityIncident; "
        f"rule_name={rule_name!r}; "
        f"org=Organization.objects.filter(slug={ORG_SLUG!r}).first(); "
        "assert org; "
        "AlertRule.objects.filter(organization=org, name=rule_name).delete(); "
        "SecurityIncident.objects.filter(organization=org, title='Alert: '+rule_name).delete(); "
        "rule=AlertRule.objects.create("
        "organization=org, name=rule_name, metric='block_rate', operator='gt', "
        "threshold=50, window_seconds=3600, severity='high', enabled=True"
        "); "
        "EnforcementEvent.objects.create(organization=org, action=ACTION_BLOCK, metadata={'threat_type':'prompt_injection'}); "
        "EnforcementEvent.objects.create(organization=org, action=ACTION_BLOCK, metadata={'threat_type':'prompt_injection'}); "
        "EnforcementEvent.objects.create(organization=org, action=ACTION_BLOCK, metadata={'threat_type':'prompt_injection'}); "
        "evaluate_alert_rules(org_id=org.id); "
        "inc=SecurityIncident.objects.filter(organization=org, title='Alert: '+rule_name).first(); "
        "assert inc, 'alert incident missing'; "
        "print(inc.id)"
    )
    return parse_last_int(docker_shell(py))


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = ""):
        results.append((name, ok, detail))
        status = "PASS" if ok else "FAIL"
        suffix = f" — {detail}" if detail else ""
        print(f"{status} {name}{suffix}")

    print("=== Step 1: API schema & contract ===")
    code, tok = request("POST", f"{BASE}/api/auth/token/", body={"email": EMAIL, "password": PASSWORD})
    check("auth_token", code == 200 and bool(tok.get("access")), f"HTTP {code}")
    if not tok.get("access"):
        return 1
    auth = {"Authorization": f"Bearer {tok['access']}"}

    code, baseline = request("GET", f"{LIST_URL}?queue=active&page=1&page_size=10", headers=auth)
    check("GET incidents queue=active", code == 200, f"HTTP {code}")
    summary = baseline.get("summary") or {}
    required_summary = (
        "total",
        "open",
        "investigating",
        "escalated",
        "resolved",
        "active",
        "critical_high",
        "by_source",
    )
    missing_summary = [k for k in required_summary if k not in summary]
    check("summary keys", not missing_summary, ", ".join(missing_summary) or "ok")
    pagination_keys = ("count", "page", "page_size", "total_pages", "results")
    missing_page = [k for k in pagination_keys if k not in baseline]
    check("pagination keys", not missing_page, ", ".join(missing_page) or "ok")
    check("summary.total is int", isinstance(summary.get("total"), int))
    check("by_source is dict", isinstance(summary.get("by_source"), dict))

    for query in ("status=bad", "severity=urgent", "source=foo", "queue=everything"):
        bad_code, _ = request("GET", f"{LIST_URL}?{query}", headers=auth)
        check(f"reject invalid filter {query}", bad_code == 400, f"HTTP {bad_code}")

    print("\n=== Step 2: Alert rule auto-incident ===")
    try:
        alert_incident_id = fire_alert_rule_incident(ALERT_RULE_NAME)
        check("evaluate_alert_rules creates incident", alert_incident_id > 0, f"id={alert_incident_id}")
    except Exception as exc:
        check("evaluate_alert_rules creates incident", False, str(exc))
        alert_incident_id = 0

    if alert_incident_id:
        code, alert_search = request(
            "GET", f"{LIST_URL}?search={quote('Alert: ' + ALERT_RULE_NAME)}", headers=auth
        )
        alert_row = next((r for r in alert_search.get("results", []) if r.get("id") == alert_incident_id), {})
        check("alert incident searchable", bool(alert_row), f"id={alert_incident_id}")
        check("alert incident status open", alert_row.get("status") == "open", alert_row.get("status", "missing"))

    print("\n=== Step 3: Manual probe incident + forensics ===")
    try:
        incident_id = create_probe_incident(PROBE_TITLE)
        check("create_probe_incident", incident_id > 0, f"id={incident_id}")
    except Exception as exc:
        check("create_probe_incident", False, str(exc))
        incident_id = 0

    if not incident_id:
        passed = sum(1 for _, ok, _ in results if ok)
        failed = sum(1 for _, ok, _ in results if not ok)
        print(f"\n=== Result: {passed}/{passed + failed} PASS ===")
        return 1

    time.sleep(0.5)
    before_mut_snap = summary_snapshot(baseline)

    code, search = request("GET", f"{LIST_URL}?search={quote(PROBE_TITLE)}", headers=auth)
    check("search finds probe", code == 200 and any(r.get("id") == incident_id for r in search.get("results", [])))
    row = next((r for r in search.get("results", []) if r.get("id") == incident_id), {})
    check("probe source lane", row.get("source") == "threat_intel", row.get("source", "missing"))
    check("probe status open", row.get("status") == "open", row.get("status", "missing"))

    code, detail = request("GET", f"{BASE}/api/module2/incidents/{incident_id}/", headers=auth)
    check("GET incident detail", code == 200, f"HTTP {code}")
    check("detail incident id", (detail.get("incident") or {}).get("id") == incident_id)
    check("detail source", detail.get("source") == "threat_intel", detail.get("source", "missing"))
    check("detail timeline", bool(detail.get("timeline")), f"len={len(detail.get('timeline') or [])}")
    evidence = detail.get("evidence") or {}
    check("detail evidence model", evidence.get("model") == "gpt-4o", evidence.get("model", "missing"))

    print("\n=== Step 4: Investigate -> escalate -> resolve ===")
    code, inv = request(
        "POST",
        f"{BASE}/api/security/incidents/{incident_id}/investigate-incident/",
        headers=auth,
        body={"notes": "M2.6 E2E investigate"},
    )
    check(
        "POST investigate-incident",
        code == 200 and inv.get("status") == "investigating",
        f"HTTP {code} status={inv.get('status')}",
    )
    check("investigate assigns analyst", bool(inv.get("assigned_to_username")), inv.get("assigned_to_username", "missing"))

    code, after_inv_list = request("GET", LIST_URL, headers=auth)
    after_inv = summary_snapshot(after_inv_list)
    check(
        "summary investigating increments",
        after_inv["investigating"] >= before_mut_snap["investigating"] + 1,
        f"{before_mut_snap['investigating']} -> {after_inv['investigating']}",
    )

    code, inv_filter = request("GET", f"{LIST_URL}?status=investigating&search={quote(PROBE_TITLE)}", headers=auth)
    check(
        "investigating filter lists probe",
        code == 200 and any(r.get("id") == incident_id for r in inv_filter.get("results", [])),
    )

    code, esc = request(
        "POST",
        f"{BASE}/api/security/incidents/{incident_id}/escalate-incident/",
        headers=auth,
        body={"notes": "M2.6 E2E escalate"},
    )
    check("POST escalate-incident", code == 200 and esc.get("status") == "escalated", f"HTTP {code} status={esc.get('status')}")

    code, after_esc_list = request("GET", LIST_URL, headers=auth)
    after_esc = summary_snapshot(after_esc_list)
    check(
        "summary escalated increments",
        after_esc["escalated"] >= before_mut_snap["escalated"] + 1,
        f"{before_mut_snap['escalated']} -> {after_esc['escalated']}",
    )

    code, res = request(
        "POST",
        f"{BASE}/api/security/incidents/{incident_id}/resolve-incident/",
        headers=auth,
        body={"notes": "M2.6 E2E resolve"},
    )
    check("POST resolve-incident", code == 200 and res.get("status") == "resolved", f"HTTP {code}")
    check("resolved_at set", bool(res.get("resolved_at")))

    code, after_res_list = request("GET", LIST_URL, headers=auth)
    after_res = summary_snapshot(after_res_list)
    check(
        "summary resolved increments",
        after_res["resolved"] >= before_mut_snap["resolved"] + 1,
        f"{before_mut_snap['resolved']} -> {after_res['resolved']}",
    )

    code, filter_resolved = request(
        "GET", f"{LIST_URL}?status=resolved&search={quote(PROBE_TITLE)}", headers=auth
    )
    check(
        "resolved filter lists probe",
        code == 200 and any(r.get("id") == incident_id for r in filter_resolved.get("results", [])),
    )

    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"\n=== Result: {passed}/{passed + failed} PASS ===")
    print(f"PROBE_TITLE={PROBE_TITLE}")
    print(f"INCIDENT_ID={incident_id}")
    print(f"ALERT_INCIDENT_ID={alert_incident_id}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
