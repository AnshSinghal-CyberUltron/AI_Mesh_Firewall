"""PR #113 (author, answering Reviewer A): apply ONE product-code mutation.

usage: python -I mutate_targeted.py <control/ai_mesh_control dir> <mutation id> | x --list
Restore with `git checkout -- .` in the worktree afterwards.
Anchors for M2a-M2d, M4a, M4b and M4d are Reviewer A's (review 5869548782);
M2e is A's queue-key drift; M4ab applies M4a and M4b together, as the PR's
original mutation check did.
"""
import pathlib
import sys

M4A = ("auth/utils.py",
       "    if is_platform_operator(request.user):\n",
       "    if is_platform_operator(request.user) or request.user.is_superuser:\n")
M4B = ("policy/compile_views.py",
       "        if not is_platform_operator(request.user):\n",
       "        if not (is_platform_operator(request.user) or request.user.is_superuser):\n")

MUTATIONS = {
    "M2e_producer_queue_key_drift": [(
        "core/ingestion_views.py",
        'REDIS_TELEMETRY_KEY = "telemetry:events"\n',
        'REDIS_TELEMETRY_KEY = "event_queue"\n',
    )],
    "M4a_orgres_superuser_crossorg": [M4A],
    "M4b_compile_guard_superuser_exempt_ONLY": [M4B],
    "M4ab_both_together": [M4A, M4B],
    "M4d_operator_without_staff": [(
        "auth/models.py",
        '    if not getattr(user, "is_staff", False):\n        return False\n',
        "",
    )],
    "M2a_ingest_toplevel_org_from_body": [(
        "core/ingestion_views.py",
        '        "organization_id": organization_id,\n        "event_type": event.get("event_type", "request"),',
        ('        "organization_id": data.get("organization_id", organization_id),\n'
         '        "event_type": event.get("event_type", "request"),'),
    )],
    "M2b_ingest_metadata_org_from_body": [(
        "core/ingestion_views.py",
        '            "organization_id": organization_id,\n            "extra": data,',
        '            "organization_id": data.get("organization_id", organization_id),\n            "extra": data,',
    )],
    "M2c_inactive_org_key_accepted": [
        ("core/agent_auth.py",
         ("org_key = OrganizationAgentKey.objects.filter(key_hash=key_hash, is_active=True)"
          '.select_related("organization").first()'),
         ("org_key = OrganizationAgentKey.objects.filter(key_hash=key_hash)"
          '.select_related("organization").first()')),
        ("core/agent_auth.py",
         ("        org_key = OrganizationAgentKey.objects.filter(\n"
          "            key_hash=key_hash, is_active=True\n        ).select_related"),
         ("        org_key = OrganizationAgentKey.objects.filter(\n"
          "            key_hash=key_hash\n        ).select_related")),
    ],
    "M2d_ingest_global_key_reported_persisted": [(
        "core/ingestion_views.py",
        "        persisted = bool(org_id)\n",
        "        persisted = True\n",
    )],
}


def main() -> None:
    root = pathlib.Path(sys.argv[1])
    name = sys.argv[2]
    for rel, old, new in MUTATIONS[name]:
        path = root / rel
        text = path.read_text()
        count = text.count(old)
        if count != 1:
            raise SystemExit(f"{name}: anchor found {count} times in {rel}")
        path.write_text(text.replace(old, new))
    print("applied", name)


if __name__ == "__main__":
    if sys.argv[2:] == ["--list"]:
        print("\n".join(MUTATIONS))
    else:
        main()
