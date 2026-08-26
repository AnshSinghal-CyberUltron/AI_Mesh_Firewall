"""T-C2: compare raw vs rollup JSON for the four Overview heavies (frozen clock)."""

from __future__ import annotations

import json
import os
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone
from rest_framework.test import APIClient

_PERIODS = ("1h", "6h", "24h", "7d", "30d")
_ENDPOINTS = (
    "/api/security/soc-kpis/?period={p}",
    "/api/security/module-kpis/?period={p}",
    "/api/security/attack-vector-trends/?period={p}",
    "/api/security/module-trends/?period={p}",
)


class Command(BaseCommand):
    help = "T-C2 oracle: 5 periods × 4 endpoints, raw vs ANALYTICS_SERVE_ROLLUPS."

    def add_arguments(self, parser):
        parser.add_argument("--rounds", type=int, default=3)
        parser.add_argument("--email", default=os.environ.get("TEST_EMAIL", "admin@zeroshield.io"))
        parser.add_argument("--out", default="")

    def handle(self, *args, **options):
        User = get_user_model()
        user = User.objects.filter(email=options["email"]).first()
        if user is None:
            user = User.objects.filter(is_superuser=True).first()
        if user is None:
            raise SystemExit("No user found for T-C2 compare.")
        client = APIClient()
        client.defaults["HTTP_HOST"] = "localhost"
        client.force_authenticate(user=user)
        prev = os.environ.get("ANALYTICS_SERVE_ROLLUPS")
        rounds = []
        try:
            for n in range(1, max(int(options["rounds"]), 1) + 1):
                frozen = timezone.now()
                mismatches = []
                samples = 0
                with mock.patch("django.utils.timezone.now", return_value=frozen):
                    for period in _PERIODS:
                        os.environ["ANALYTICS_SERVE_ROLLUPS"] = "0"
                        raws = {}
                        for ep in _ENDPOINTS:
                            path = ep.format(p=period)
                            resp = client.get(path)
                            if resp.status_code != 200:
                                mismatches.append({"period": period, "path": path, "raw_status": resp.status_code})
                                continue
                            raws[ep] = resp.json()
                        os.environ["ANALYTICS_SERVE_ROLLUPS"] = "1"
                        for ep in _ENDPOINTS:
                            samples += 1
                            path = ep.format(p=period)
                            if ep not in raws:
                                continue
                            resp = client.get(path)
                            if resp.status_code != 200:
                                mismatches.append({"period": period, "path": path, "rolled_status": resp.status_code})
                                continue
                            rolled = resp.json()
                            if rolled != raws[ep]:
                                mismatches.append(
                                    {
                                        "period": period,
                                        "path": path,
                                        "raw_keys": sorted(raws[ep].keys())
                                        if isinstance(raws[ep], dict)
                                        else type(raws[ep]).__name__,
                                        "rolled_keys": sorted(rolled.keys())
                                        if isinstance(rolled, dict)
                                        else type(rolled).__name__,
                                    }
                                )
                rounds.append(
                    {
                        "round": n,
                        "samples": samples,
                        "mismatches": mismatches,
                        "ok": samples >= 20 and not mismatches,
                    }
                )
                self.stdout.write(
                    f"round {n}: samples={samples} mismatches={len(mismatches)} ok={rounds[-1]['ok']}"
                )
        finally:
            if prev is None:
                os.environ.pop("ANALYTICS_SERVE_ROLLUPS", None)
            else:
                os.environ["ANALYTICS_SERVE_ROLLUPS"] = prev
        payload = {"rounds": rounds, "all_ok": all(r["ok"] for r in rounds)}
        out = options["out"]
        if out:
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
        if not payload["all_ok"]:
            raise SystemExit(json.dumps(payload, indent=2))
        self.stdout.write(self.style.SUCCESS(json.dumps(payload)))
