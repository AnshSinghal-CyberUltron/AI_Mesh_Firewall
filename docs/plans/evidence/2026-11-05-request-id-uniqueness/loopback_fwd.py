"""TEST HARNESS ONLY: forwards 127.0.0.1:8000 -> control:8000 and 127.0.0.1:9500 -> fake-mcp:9500 inside the gateway-v2 container so the
GW32 credential service (which refuses a plaintext non-loopback control link) wires. Not product code."""
import asyncio
async def pipe(r, w):
    try:
        while (d := await r.read(65536)):
            w.write(d); await w.drain()
    finally:
        w.close()
def handler(host, port):
    async def h(r, w):
        ur, uw = await asyncio.open_connection(host, port)
        await asyncio.gather(pipe(r, uw), pipe(ur, w))
    return h
async def main():
    a = await asyncio.start_server(handler("control", 8000), "127.0.0.1", 8000)
    b = await asyncio.start_server(handler("fake-mcp", 9500), "127.0.0.1", 9500)
    await asyncio.gather(a.serve_forever(), b.serve_forever())
asyncio.run(main())
