"""C4 evidence: the dev provider's pool (`pool_size(PROVIDER)` connections) serialises streams.

Builds `OpenAIUpstreamProvider` exactly as `edge/dev_profile.py:302-311` does, with this host's
contract, and opens N concurrent streams against `upstream.py --delay-ms 1000` (each response is
held for 1 s). With one pooled connection the streams finish one after another.

    F1A3_BENCH_KEY=... python probe_pool.py --base http://172.17.0.1:18970/v1 --streams 4
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "gateway_v2"))

from gateway_v2.dispatch.openai_upstream import (  # noqa: E402
    BODY_ENDPOINT_KEY,
    BODY_OPENAI_KEY,
    CHAT_ENDPOINT,
    OpenAIUpstreamProvider,
    httpx_client_factory,
)
from gateway_v2.dispatch.provider import UpstreamRequest  # noqa: E402
from gateway_v2.edge.wire.sse_decode import SSEDecoder  # noqa: E402
from gateway_v2.runtime.kinds import PoolKind  # noqa: E402
from gateway_v2.runtime.resources import load_contract  # noqa: E402


async def main(base: str, streams: int) -> None:
    contract, _logs = load_contract()
    connections = contract.pool_size(PoolKind.PROVIDER)
    provider = OpenAIUpstreamProvider(
        base_url=base,
        decoder_factory=SSEDecoder,
        client_factory=httpx_client_factory(
            api_key=os.environ["F1A3_BENCH_KEY"],
            timeout_s=contract.max_stream_duration_s(),
            connections=connections,
        ),
        connections=connections,
    )
    start = time.perf_counter()

    async def one(i: int) -> float:
        body = {"model": "m", "messages": [{"role": "user", "content": "x"}], "stream": True}
        req = UpstreamRequest("probe", {BODY_OPENAI_KEY: body, BODY_ENDPOINT_KEY: CHAT_ENDPOINT})
        async for event in provider.open(req):
            assert event.gateway_error is None, event.gateway_error
        return time.perf_counter() - start

    done = await asyncio.gather(*(one(i) for i in range(streams)))
    await provider.aclose()
    print(
        f"pool_size(PROVIDER)={connections} streams={streams} upstream hold=1.0 s "
        f"completion times (s)={[round(t, 2) for t in sorted(done)]}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--streams", type=int, default=4)
    args = parser.parse_args()
    asyncio.run(main(args.base, args.streams))
