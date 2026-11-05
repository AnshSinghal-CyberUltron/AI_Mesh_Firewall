"""Simulate a non-draining client on an INCREMENTAL stream (task 5, check e sub-case).

Opens a raw HTTP/1.1 streaming chat to the incremental org-b, reads the headers + first frame,
then STOPS reading for ~8 s (> the 3 s idle bound) so the gateway's downstream send() back-
pressures. A client that stops reading must still be cut with the idle timeout. Not product code.
"""
import json
import socket
import time

HOST, PORT = "gateway-v2", 8301
BODY = json.dumps({"model": "echo-1", "stream": True,
                   "messages": [{"role": "user", "content": "SLOWSTREAM slow please"}]})
REQ = (f"POST /v1/chat/completions HTTP/1.1\r\nHost: {HOST}\r\n"
       f"Authorization: Bearer dev-peer-key\r\nContent-Type: application/json\r\n"
       f"Content-Length: {len(BODY)}\r\nConnection: close\r\n\r\n{BODY}")


def run():
    s = socket.create_connection((HOST, PORT), timeout=20)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)  # small buffer: back-pressure sooner
    except OSError:
        pass
    s.sendall(REQ.encode())
    t0 = time.time()
    s.settimeout(6)
    try:
        first = s.recv(4096)  # status line + headers + maybe the first content frame
    except Exception as exc:  # noqa: BLE001
        first = f"<recv err {type(exc).__name__}: {exc}>".encode()
    first_s = round(time.time() - t0, 3)
    # STOP reading for 8 s (> idle 3 s). The server keeps producing; its send() back-pressures.
    time.sleep(8.0)
    s.settimeout(6)
    rest = b""
    try:
        while True:
            d = s.recv(65536)
            if not d:
                break
            rest += d
    except Exception:  # noqa: BLE001
        pass
    total_s = round(time.time() - t0, 3)
    full = (first if isinstance(first, bytes) else b"") + rest
    txt = full.decode("utf-8", "replace")
    out = {
        "check": "e_client_stops_reading", "org": "org-b(incremental)",
        "first_recv_s": first_s, "total_s": total_s, "bytes": len(full),
        "status_line": txt.split("\r\n", 1)[0] if txt else "",
        "idle_in_body": "idle" in txt.lower(),
        "head": txt[:200], "tail": txt[-500:],
    }
    with open("/evidence/e-client-stall.jsonl", "w") as fh:
        fh.write(json.dumps(out) + "\n")
    print(json.dumps(out)[:900])


if __name__ == "__main__":
    run()
