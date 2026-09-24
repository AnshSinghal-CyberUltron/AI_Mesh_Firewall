#!/usr/bin/env python3
"""Generate shared/canaries.json: the canary strings shared by make_corpus, synthprov and olg.

Every value is synthetic and obviously fake (the 'rvcanary4417' / 'RVCANARY' markers, IANA
documentation ranges, the Visa test PAN, the never-issued 219-09-9999 SSN).  Each canary has
a `value` and `fragments`; a hit on ANY of them in provider-received bytes (or in client-received
content for output canaries) counts as a leak of that canary.

Classes: pii (policy REDACT), secret (policy BLOCK), injection (policy BLOCK),
output (injected BY synthprov into responses; policy REDACT at egress).
Injection texts are copied verbatim from tests/detection_corpus/malicious.jsonl (ids recorded).

Usage: python gen_canaries.py --malicious <repo>/tests/detection_corpus/malicious.jsonl --out shared/canaries.json
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from pathlib import Path


def luhn_ok(num: str) -> bool:
    digits = [int(c) for c in num if c.isdigit()]
    s = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        s += d
    return s % 10 == 0


def b64url(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


INJECTION_IDS = [
    "mal-prompt_injection-0000",
    "mal-jailbreak-0000",
    "mal-goal_hijacking-0000",
    "mal-paraphrase-0000",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--malicious", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    gh_body = "RvCanary4417" + "aB9cD8eF7gH6iJ5kL4mN3oP2"  # 36 chars after ghp_
    google_body = "SyRvCanary4417_aBcDeFgHiJkLmNoPqRsT"  # 35 chars after AIza
    jwt = ".".join([
        b64url('{"alg":"HS256","typ":"JWT"}'),
        b64url('{"sub":"rvcanary4417","name":"RV Canary"}'),
        b64url("signature-rvcanary4417"),
    ])
    pem_line = "MIIEpAIBAAKCAQEArvCanary4417Qm9Xy7Lp3Zt5Wc8Vb2Nd6Hf1Jg4Ks0Rq"
    pem = "-----BEGIN RSA PRIVATE KEY-----\n" + pem_line + "\n-----END RSA PRIVATE KEY-----"
    canaries = [
        # ---- input PII (expected REDACT under an enforcing plan) ----
        {"id": "pii.email", "class": "pii", "kind": "email",
         "value": "ana.rvcanary4417@example.org", "fragments": ["rvcanary4417@example.org"]},
        {"id": "pii.phone", "class": "pii", "kind": "phone",
         "value": "202-555-0147", "fragments": ["555-0147"]},
        {"id": "pii.ssn", "class": "pii", "kind": "ssn",
         "value": "219-09-9999", "fragments": ["219-09-9999", "219099999"]},
        {"id": "pii.card", "class": "pii", "kind": "card",
         "value": "4111 1111 1111 1111", "fragments": ["4111111111111111", "4111-1111-1111-1111"]},
        {"id": "pii.ipv4", "class": "pii", "kind": "ipv4",
         "value": "203.0.113.57", "fragments": []},
        # ---- input secrets (expected BLOCK) ----
        {"id": "secret.aws", "class": "secret", "kind": "aws_access_key",
         "value": "AKIARVCANARY4417XQ2Z", "fragments": ["RVCANARY4417XQ2Z"]},
        {"id": "secret.github", "class": "secret", "kind": "github_pat",
         "value": "ghp_" + gh_body, "fragments": [gh_body]},
        {"id": "secret.slack", "class": "secret", "kind": "slack_token",
         "value": "xoxb-441700000001-441700000002-RvCanary4417AbCdEfGhIjKl",
         "fragments": ["RvCanary4417AbCdEfGhIjKl"]},
        {"id": "secret.google", "class": "secret", "kind": "google_api_key",
         "value": "AIza" + google_body, "fragments": [google_body]},
        {"id": "secret.stripe", "class": "secret", "kind": "stripe_live_key",
         "value": "sk_live_RvCanary4417aBcDeFgHiJkL", "fragments": ["RvCanary4417aBcDeFgHiJkL"]},
        {"id": "secret.jwt", "class": "secret", "kind": "jwt",
         "value": jwt, "fragments": [jwt.split(".")[1]]},
        {"id": "secret.pem", "class": "secret", "kind": "pem_private_key",
         "value": pem, "fragments": ["-----BEGIN RSA PRIVATE KEY-----", pem_line]},
        {"id": "secret.generic", "class": "secret", "kind": "generic_api_key",
         "value": "api_key=Zq8Rv4417Lm2Xc9Tb6Wn3Hp5Jd7Kf1Gs", "fragments": ["Zq8Rv4417Lm2Xc9Tb6Wn3Hp5Jd7Kf1Gs"]},
        # ---- output canaries injected by synthprov (x-synth-inject) ----
        {"id": "out.email", "class": "output", "kind": "email",
         "value": "rv.output4417@example.net", "fragments": ["output4417@example.net"]},
        {"id": "out.aws", "class": "output", "kind": "aws_access_key",
         "value": "AKIARVOUTPUT4417ZZ9Q", "fragments": ["RVOUTPUT4417ZZ9Q"],
         "split": ["AKIARVO", "UTPUT44", "17ZZ9Q"]},
    ]
    mal = {}
    for line in Path(args.malicious).read_text().splitlines():
        d = json.loads(line)
        mal[d["id"]] = d
    for mid in INJECTION_IDS:
        text = mal[mid]["text"]
        canaries.append({"id": "inj." + mid.split("-")[1], "class": "injection", "kind": mal[mid]["family"],
                         "value": text, "fragments": [], "provenance": "tests/detection_corpus/malicious.jsonl#" + mid})

    # ---- validation of detector formats and JSON-safety ----
    problems = []
    by = {c["id"]: c for c in canaries}
    if not re.fullmatch(r"AKIA[0-9A-Z]{16}", by["secret.aws"]["value"]):
        problems.append("aws format")
    if not re.fullmatch(r"AKIA[0-9A-Z]{16}", by["out.aws"]["value"]):
        problems.append("out.aws format")
    if "".join(by["out.aws"]["split"]) != by["out.aws"]["value"] or len(by["out.aws"]["split"]) != 3:
        problems.append("out.aws split")
    if not re.fullmatch(r"ghp_[A-Za-z0-9]{36}", by["secret.github"]["value"]):
        problems.append("github format")
    if not re.fullmatch(r"AIza[0-9A-Za-z_\-]{35}", by["secret.google"]["value"]):
        problems.append("google format")
    if not re.fullmatch(r"sk_live_[0-9A-Za-z]{24,99}", by["secret.stripe"]["value"]):
        problems.append("stripe format")
    if not re.fullmatch(r"xox[baprs]-[0-9A-Za-z-]{10,}", by["secret.slack"]["value"]):
        problems.append("slack format")
    if not luhn_ok(by["pii.card"]["value"]):
        problems.append("card luhn")
    for c in canaries:
        for s in [c["value"], *c["fragments"]]:
            if c["id"] != "secret.pem" and any(ch in s for ch in '"\\\n\r\t') or not s.isascii():
                problems.append(f"{c['id']}: value would be JSON-escaped: {s!r}")
    if problems:
        print("INVALID:", problems, file=sys.stderr)
        return 2
    doc = {"generated_by": "gen_canaries.py", "canaries": canaries,
           "note": "all values synthetic; secret.pem contains newlines so only its fragments can match raw JSON bytes"}
    Path(args.out).write_text(json.dumps(doc, indent=1) + "\n")
    print(f"wrote {len(canaries)} canaries", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
