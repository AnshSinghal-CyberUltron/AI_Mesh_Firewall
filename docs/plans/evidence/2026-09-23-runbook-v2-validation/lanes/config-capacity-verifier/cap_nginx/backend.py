"""Fake gateway:8300 — counts TCP connections and records the Connection header nginx sends."""
import asyncio, json, sys
PORT = int(sys.argv[1]); OUT = sys.argv[2]
stats = {"connections": 0, "requests": 0, "conn_headers": {}}
async def handle(reader, writer):
    stats["connections"] += 1
    try:
        while True:
            head = await reader.readuntil(b"\r\n\r\n")
            stats["requests"] += 1
            conn = "<absent>"
            for line in head.decode().split("\r\n")[1:]:
                if line.lower().startswith("connection:"):
                    conn = line.split(":", 1)[1].strip()
            stats["conn_headers"][conn] = stats["conn_headers"].get(conn, 0) + 1
            body = b'{"ok":true}'
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body))
            await writer.drain()
            with open(OUT, "w") as f: json.dump(stats, f)
    except (asyncio.IncompleteReadError, ConnectionResetError):
        pass
    finally:
        writer.close()
async def main():
    srv = await asyncio.start_server(handle, "127.0.0.1", PORT)
    async with srv: await srv.serve_forever()
asyncio.run(main())
