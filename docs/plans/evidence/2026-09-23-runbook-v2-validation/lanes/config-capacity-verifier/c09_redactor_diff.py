"""GW09: the decision redactor vs the wire redactor produce different bytes.

DECISION (main.py:8583 + 9559): prompt = _extract_prompt_from_messages(messages)
    redacted_prompt = INPUT_SCANNER.redact_pii(prompt, verdict=verdict)
WIRE (llm_router.py:1013-1124): LLMRouter._apply_redaction(body, redacted_content)
    -> per message (system skipped at :1077) -> _redact_text_with_backstop
    -> patterns.redact_all + 7+-digit backstop (docstring :490 "by construction WEAKER").
Executes the real functions; the "model sees" column concatenates text parts with no
separator, as the OpenAI API does (main.py:1722-1724 G70 comment).
"""
import asyncio
import os

os.environ.setdefault("ENABLE_TIER2", "false")

import ai_mesh_gateway.main as m
from ai_mesh_gateway.llm_router import LLMRouter, _redact_text_with_backstop
from scanner import InputScanner

SC = InputScanner(thread_pool_size=1, config={})


def model_view(msg):
    c = msg.get("content")
    if isinstance(c, list):
        return "".join(p.get("text", "") for p in c if isinstance(p, dict))
    return c or ""


CASES = {
    "1 SSN split across two text parts (G70)": [
        {"role": "user", "content": [{"type": "text", "text": "Please file this. My SSN is 123-"},
                                     {"type": "text", "text": "45-6789 thanks"}]},
    ],
    "2 PII inside the system message": [
        {"role": "system", "content": "You are a support agent. Customer: Jane Roe, SSN 123-45-6789, jane.roe@example.com"},
        {"role": "user", "content": "Summarise the customer's record."},
    ],
    "3 phone label in one turn, number in the next": [
        {"role": "user", "content": "My phone number is"},
        {"role": "user", "content": "9876543210"},
    ],
    "4 plain SSN in one user turn (control)": [
        {"role": "user", "content": "My SSN is 123-45-6789, please update it."},
    ],
}


async def main():
    for label, msgs in CASES.items():
        prompt = m._extract_prompt_from_messages(msgs)
        verdict = await SC.scan_prompt(prompt)
        decision = SC.redact_pii(prompt, verdict=verdict)
        wire_body = LLMRouter._apply_redaction(None, {"model": "x", "messages": msgs}, decision)
        wire_view = [f"[{w['role']}] {model_view(w)}" for w in wire_body["messages"]]
        print(f"=== {label}")
        print(f"  Tier-1 verdict           : action={verdict.action} threat={verdict.threat_type} patterns={verdict.matched_patterns}")
        print(f"  DECISION redacted_prompt : {decision!r}")
        print(f"  WIRE (what model sees)   : {wire_view!r}")
        raw_left = [v for v in ("123-45-6789", "9876543210", "jane.roe@example.com") if any(v in x for x in wire_view)]
        print(f"  raw values on the wire   : {raw_left}   | decision contains them: "
              f"{[v for v in raw_left if v in decision]}")
        # main.py:9592 B1 fail-closed check runs on the FLATTENED prompt, not the wire body
        b1_noop = _redact_text_with_backstop(prompt, decision) == prompt
        print(f"  B1 check (main.py:9592) redaction_noop={b1_noop} -> fail-closed block armed: {b1_noop}")


asyncio.run(main())
