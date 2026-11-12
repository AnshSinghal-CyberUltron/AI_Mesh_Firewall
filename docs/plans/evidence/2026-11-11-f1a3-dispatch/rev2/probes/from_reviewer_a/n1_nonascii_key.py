# Provenance: written by PR #121 Plan Reviewer A (review 5869433944) on the review host and copied
# unchanged below this header for the F1a-3 revision 2 evidence, with credit. Originally
# scripts/test_repro_develop.py (renamed so no pytest collector picks it up) or scripts/n1_nonascii_key.py.
import asyncio
from gateway_v2.dispatch.openai_upstream import OpenAIUpstreamProvider, httpx_client_factory, BODY_OPENAI_KEY, BODY_ENDPOINT_KEY
from gateway_v2.dispatch.provider import UpstreamRequest
from gateway_v2.edge.wire.sse_decode import SSEDecoder
p = OpenAIUpstreamProvider(base_url="http://127.0.0.1:9/v1", decoder_factory=SSEDecoder,
    client_factory=httpx_client_factory(api_key="sk-é–key", timeout_s=1, connections=1))
req = UpstreamRequest("t", {BODY_OPENAI_KEY: {"model": "m", "messages": []}, BODY_ENDPOINT_KEY: "chat/completions"})
async def main():
    try:
        async for e in p.open(req):
            print("event", e.gateway_error)
    except Exception as exc:
        print("ESCAPED", type(exc).__name__)
asyncio.run(main())
