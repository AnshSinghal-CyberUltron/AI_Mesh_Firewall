"""GW09 control: same single-user-message text through both redactors (real functions)."""
import asyncio, os
os.environ.setdefault("ENABLE_TIER2", "false")
import ai_mesh_gateway.main as m
from ai_mesh_gateway.llm_router import LLMRouter
from scanner import InputScanner
SC = InputScanner(thread_pool_size=1, config={})
SAMPLES = [
    "email me at john.doe@acme.com", "call +1 (415) 555-0132 today", "phone: 9876543210",
    "card 4111 1111 1111 1111 exp 12/29", "ssn 123-45-6789", "key AKIAIOSFODNN7EXAMPLE",
    "token ghp_abcdefghijklmnopqrstuvwxyz0123456789", "IBAN GB82 WEST 1234 5698 7654 32",
    "passport number X12345678", "my password is hunter2!", "server at 10.0.3.17 port 22",
    "acct 12345678901 routing 021000021",
]
async def main():
    diff = 0
    for t in SAMPLES:
        msgs = [{"role": "user", "content": t}]
        prompt = m._extract_prompt_from_messages(msgs)
        v = await SC.scan_prompt(prompt)
        dec = SC.redact_pii(prompt, verdict=v)
        wire = LLMRouter._apply_redaction(None, {"messages": msgs}, dec)["messages"][0]["content"]
        same = dec == f"[user]: {wire}"
        diff += (not same)
        print(f"{'SAME' if same else 'DIFF'} | verdict={v.action:6} | decision={dec[8:]!r} | wire={wire!r}")
    print("differences:", diff, "of", len(SAMPLES))
asyncio.run(main())
