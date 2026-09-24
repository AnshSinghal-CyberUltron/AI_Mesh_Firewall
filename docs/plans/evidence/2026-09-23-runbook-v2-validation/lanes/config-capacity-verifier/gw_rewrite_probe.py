"""§10.1.5 'Firewall-triggered generation': drive the REAL rewrite helpers.

Non-stream: main._rewrite_output_response_text_via_router -> LLM_ROUTER.acompletion
Stream:     secure_streaming DONE-flush calls output_guard.rewrite_output_response_text
            (original_text=..., NO bedrock_client) -> static canned text, no model call.
"""
import asyncio
import json

import ai_mesh_gateway.main as m


class FakeRouter:
    def __init__(self):
        self.calls = []

    async def acompletion(self, body, api_key):
        self.calls.append(json.loads(json.dumps(body)))
        return 200, {"choices": [{"message": {"role": "assistant",
                                              "content": "The customer's record is on file."}}]}

    async def acompletion_stream(self, *a, **k):  # pragma: no cover - must not be used
        raise AssertionError("stream path not expected")


async def main():
    fake = FakeRouter()
    m.LLM_ROUTER = fake
    customer_body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Summarise the ticket"}],
        "_zs_org_slug": "acme",
    }
    model_output = "Ticket #42: customer SSN 123-45-6789, email bob@example.com, card 4111 1111 1111 1111."
    text, reinferred = await m._rewrite_output_response_text_via_router(
        "pii", "PII in output", model_output, customer_body
    )
    print("NON-STREAM rewrite result:", repr(text), "| reinferred:", reinferred)
    print("LLM_ROUTER.acompletion calls made by the firewall:", len(fake.calls))
    for c in fake.calls:
        print("  model:", c["model"], "| org:", c["_zs_org_slug"], "| max_tokens:", c["max_tokens"],
              "| routing_preferences:", c["routing_preferences"])
        for msg in c["messages"]:
            print(f"  [{msg['role']}] {msg['content'][:400]!r}")
    print("customer's own messages:", customer_body["messages"])

    # Streaming path: exactly what secure_streaming.py:527 does at DONE flush.
    from output_guard import rewrite_output_response_text
    fake.calls.clear()
    s = rewrite_output_response_text("pii", "PII in output", original_text=model_output)
    print("STREAM rewrite (secure_streaming.py:527 call shape) ->", repr(s))
    print("LLM_ROUTER.acompletion calls on stream path:", len(fake.calls))


asyncio.run(main())
