"""§10.1.5 row 3: tier2_enabled=None -> input Tier-2 OFF, output Tier-2 ON.

INPUT: the chat path's single gate is main.py:9112
    resolved_tier2_enabled = resolve_tier2_enabled(org_config.get("tier2_enabled"))
OUTPUT: OutputGuard.inspect -> _enabled("output_tier2_enabled", True) ->
    InputScanner.scan_output_with_tier2(org_tier2_override=None) which only
    returns early when `override is None and not self.tier2_enabled` (ENABLE_TIER2).
Drives the REAL InputScanner + OutputGuard with a recording fake Bedrock scanner.
"""
import asyncio
import os
import sys

os.environ["ENABLE_TIER2"] = sys.argv[1] if len(sys.argv) > 1 else "true"  # compose default: true
os.environ["GATEWAY_TIER2_CACHE_TTL_SECONDS"] = "0"

import config_sync
import scanner as scanner_mod
from output_guard import OutputGuard
from config import load_config


class RecordingBedrock:
    model = "fake-guard-model"

    def __init__(self):
        self.calls = []

    async def ascan(self, text, context=None, request_id=None):
        self.calls.append(("ascan", text[:60]))
        return {"meta": {"recommended_action": "allow"}, "llm_guard": {"score": 0.0}}

    def scan(self, text, context=None, request_id=None):
        self.calls.append(("scan", text[:60]))
        return {"meta": {"recommended_action": "allow"}, "llm_guard": {"score": 0.0}}


async def main():
    CONFIG = load_config()
    s = scanner_mod.InputScanner(thread_pool_size=2, config=CONFIG)
    fake = RecordingBedrock()
    s._bedrock_scanner = fake  # replace the real boto3-backed scanner
    og = OutputGuard(scanner=s, config=CONFIG)

    # A tenant row exactly as the control plane publishes it: tier2_enabled=None
    org_config = {"tier2_enabled": None, "enforcement_mode": "block"}
    print(f"ENABLE_TIER2={os.environ['ENABLE_TIER2']} -> InputScanner.tier2_enabled={s.tier2_enabled}")

    # INPUT gate (main.py:9105-9112)
    resolved = config_sync.resolve_tier2_enabled(org_config.get("tier2_enabled"))
    print("INPUT : resolve_tier2_enabled(None) =", resolved, "-> chat path runs Tier-1-only scan_prompt")
    fake.calls.clear()
    await s.scan_prompt("What is the capital of France?")
    print("INPUT : guard-model calls during Tier-1 scan_prompt =", len(fake.calls))

    # OUTPUT (same request, same org_config)
    fake.calls.clear()
    v = await og.inspect("Paris is the capital of France.", context_chunks=[], org_config=org_config,
                         org_slug="acme")
    print("OUTPUT: OutputGuard.inspect(org tier2_enabled=None) guard-model calls =", len(fake.calls),
          fake.calls, "| verdict action =", getattr(v, "action", v))

    # Explicit False does switch output Tier-2 off (only None is asymmetric).
    fake.calls.clear()
    await og.inspect("Paris is the capital of France.", context_chunks=[], org_config={"tier2_enabled": False},
                     org_slug="acme")
    print("OUTPUT: with tier2_enabled=False guard-model calls =", len(fake.calls))


asyncio.run(main())
