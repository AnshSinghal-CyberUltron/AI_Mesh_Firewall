"""Apply (or check) one-line mutations that undo each security property.

usage: python mutate.py apply <control/ai_mesh_control dir>
Restore afterwards with `git checkout --` on the listed files (none are changed by the PR).
"""
import pathlib
import sys

MUTATIONS = {
    "M1 key-owner org check removed": (
        "core/gateway_key_views.py",
        "if profile and profile.organization_id == obj.organization_id:",
        "if profile:",
    ),
    "M2 ingestion org taken from body": (
        "core/ingestion_views.py",
        '        "organization_id": organization_id,\n        "event_type": event.get("event_type", "request"),',
        '        "organization_id": (event.get("data") or {}).get("organization_id", organization_id),\n'
        '        "event_type": event.get("event_type", "request"),',
    ),
    "M3 global kill-switch refusal removed": (
        "core/serializers.py",
        "        if model_name == KillSwitch.SCOPE_GLOBAL:\n            raise serializers.ValidationError(",
        "        if False and model_name == KillSwitch.SCOPE_GLOBAL:\n            raise serializers.ValidationError(",
    ),
    "M4a superuser cross-org (org resolution)": (
        "auth/utils.py",
        "    if is_platform_operator(request.user):\n",
        "    if is_platform_operator(request.user) or request.user.is_superuser:\n",
    ),
    "M4b superuser cross-org (compile guard)": (
        "policy/compile_views.py",
        "        if not is_platform_operator(request.user):\n",
        "        if not (is_platform_operator(request.user) or request.user.is_superuser):\n",
    ),
    "M5 isolation recipients not org-filtered": (
        "core/isolation_notify.py",
        "            if org_id is not None:\n                admin_qs = admin_qs.filter(",
        "            if False:\n                admin_qs = admin_qs.filter(",
    ),
    "M6 module-1.6 audit feed not org-filtered": (
        "policy/security_views.py",
        "KillSwitchAuditLog.objects.filter(organization=org, timestamp__gte=since)",
        "KillSwitchAuditLog.objects.filter(timestamp__gte=since)",
    ),
    "M7 SSRF private-address check removed": (
        "mcp_connector/_url_guard.py",
        "    if ip.is_private:\n",
        "    if False and ip.is_private:\n",
    ),
}


def main() -> None:
    root = pathlib.Path(sys.argv[2])
    for name, (rel, old, new) in MUTATIONS.items():
        path = root / rel
        text = path.read_text()
        assert text.count(old) == 1, (name, text.count(old))
        path.write_text(text.replace(old, new))
        print("applied", name, rel)


if __name__ == "__main__":
    main()
