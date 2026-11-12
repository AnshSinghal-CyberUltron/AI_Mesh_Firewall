"""F1a-3 rev 2 probe (B-B3, B-B4): the hardened aiohttp transport settings, on the pinned version.

Checks, against a local raw-socket server that records every accepted connection and request head:
  0. Control: with no socket_factory an IP-literal URL to a listening 127.0.0.2 connects
     (the resolver is never called), reproducing Reviewer B's finding on the pinned version.
  1. IP-literal URL: aiohttp skips the resolver for literals (connector._resolve_host), but the
     public `socket_factory` hook still runs for every dial. A factory that refuses an address not
     in the checked set stops the dial before any TCP SYN: the server must see 0 connections.
  2. Hostname: the resolver returns the checked set; the factory accepts; request succeeds.
  3. Resolver answers a refused address (rebinding double): factory refuses, 0 connections.
  4. allow_redirects=False: a 302 to another origin is returned, never followed.
  5. auto_decompress=False + Accept-Encoding: identity: the header on the wire is `identity`;
     a gzip-encoded 200 is detected from the response head and refused BEFORE reading the body;
     process RSS does not grow by the inflated size.
  6. trust_env=False with HTTP(S)_PROXY set: the request still goes direct.
Run: python -I aiohttp_transport_guard.py   (prints JSON lines; exit 0 iff every check passes)
"""
from __future__ import annotations

import asyncio
import gzip
import ipaddress
import json
import os
import resource
import socket
import sys

import aiohttp
from aiohttp.abc import AbstractResolver

ALLOWED = {ipaddress.ip_address("127.0.0.1")}
SEEN: dict[str, list] = {"conns": [], "heads": []}
BOMB = gzip.compress(b"\0" * (64 * 1024 * 1024), compresslevel=9)  # 64 MiB inflated


class Refused(OSError):
    pass


def socket_factory(addr_info):  # type: ignore[no-untyped-def]
    family, type_, proto, _canon, sockaddr = addr_info
    ip = ipaddress.ip_address(sockaddr[0])
    if ip not in ALLOWED:
        raise Refused(f"dial refused: {ip}")
    return socket.socket(family=family, type=type_, proto=proto)


class CheckedResolver(AbstractResolver):
    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers
        self.calls = 0

    async def resolve(self, host, port=0, family=socket.AF_INET):  # type: ignore[no-untyped-def]
        self.calls += 1
        if host not in self.answers:
            raise OSError(f"no answer for {host}")
        return [{"hostname": host, "host": self.answers[host], "port": port,
                 "family": socket.AF_INET, "proto": 0, "flags": socket.AI_NUMERICHOST}]

    async def close(self) -> None:
        return None


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    SEEN["conns"].append(writer.get_extra_info("peername")[0])
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except asyncio.IncompleteReadError:
        writer.close()
        return
    text = head.decode("latin-1")
    SEEN["heads"].append(text)
    path = text.split(" ", 2)[1]
    n = 0
    for line in text.split("\r\n"):
        if line.lower().startswith("content-length:"):
            n = int(line.split(":", 1)[1])
    if n:
        await reader.readexactly(n)
    if path.startswith("/redirect"):
        writer.write(b"HTTP/1.1 302 Found\r\nlocation: http://169.254.169.254/latest/meta-data/\r\n"
                     b"content-length: 0\r\n\r\n")
    elif path.startswith("/gzip"):
        writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\ncontent-encoding: gzip\r\n"
                     b"content-length: " + str(len(BOMB)).encode() + b"\r\n\r\n" + BOMB)
    else:
        body = b"data: [DONE]\n\n"
        writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\ncontent-length: "
                     + str(len(body)).encode() + b"\r\n\r\n" + body)
    await writer.drain()
    writer.close()


def rss_kib() -> int:
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    return -1


async def main() -> int:
    os.environ["HTTP_PROXY"] = os.environ["HTTPS_PROXY"] = "http://10.255.255.1:3128"
    srv = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    # a second listener on 127.0.0.2 (NOT in the checked set) so a dial there would succeed
    srv2 = await asyncio.start_server(handle, "127.0.0.2", port)
    resolver = CheckedResolver({"provider.test": "127.0.0.1", "rebind.test": "127.0.0.2"})
    # control: without socket_factory the IP literal reaches the server (Reviewer B's B4 finding)
    ctl = aiohttp.ClientSession(connector=aiohttp.TCPConnector(resolver=CheckedResolver({}),
                                use_dns_cache=False, force_close=True), trust_env=False)
    before = len(SEEN["conns"])
    async with ctl.post(f"http://127.0.0.2:{port}/v1/chat/completions", data=b"{}") as r:
        ctl_status = r.status
    await ctl.close()
    control = {"check": "0_control_literal_without_factory", "status": ctl_status,
               "server_connections": len(SEEN["conns"]) - before,
               "pass": ctl_status == 200 and len(SEEN["conns"]) - before == 1}
    print(json.dumps(control), flush=True)
    conn = aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False, socket_factory=socket_factory,
                                limit=8, force_close=True)
    sess = aiohttp.ClientSession(connector=conn, trust_env=False, auto_decompress=False,
                                 max_line_size=8190, max_field_size=8190,
                                 skip_auto_headers=("Accept-Encoding", "User-Agent"),
                                 timeout=aiohttp.ClientTimeout(total=10))
    hdrs = {"accept-encoding": "identity", "content-type": "application/json"}
    results = [control]
    heads_from = len(SEEN["heads"])  # only the hardened session's request heads are checked in 6

    async def attempt(name: str, url: str, expect: str) -> None:
        before = len(SEEN["conns"])
        rss0 = rss_kib()
        outcome = ""
        try:
            async with sess.post(url, data=b"{}", headers=hdrs, allow_redirects=False) as r:
                enc = r.headers.get("content-encoding", "identity").lower()
                if enc != "identity":
                    outcome = f"refused_encoding:{enc}"  # before any body read
                else:
                    await r.read()
                    outcome = f"status:{r.status}"
        except Exception as exc:  # noqa: BLE001 - probe records the type only
            outcome = f"exc:{type(exc).__name__}"
        res = {"check": name, "url_host": url.split('/')[2], "outcome": outcome,
               "server_connections": len(SEEN["conns"]) - before,
               "rss_growth_kib": rss_kib() - rss0, "pass": outcome.startswith(expect)}
        results.append(res)
        print(json.dumps(res), flush=True)

    await attempt("1_ip_literal_refused", f"http://127.0.0.2:{port}/v1/chat/completions", "exc:")
    await attempt("2_hostname_allowed", f"http://provider.test:{port}/v1/chat/completions", "status:200")
    await attempt("3_rebind_refused", f"http://rebind.test:{port}/v1/chat/completions", "exc:")
    await attempt("4_redirect_not_followed", f"http://provider.test:{port}/redirect", "status:302")
    await attempt("5_gzip_refused_before_body", f"http://provider.test:{port}/gzip", "refused_encoding:gzip")
    ae = [h for h in SEEN["heads"][heads_from:] if "accept-encoding" in h.lower()]
    wire_ae = sorted({line.split(":", 1)[1].strip() for h in ae for line in h.split("\r\n")
                      if line.lower().startswith("accept-encoding:")})
    extra = {"check": "6_wire_accept_encoding_and_direct", "accept_encoding_values": wire_ae,
             "every_connection_direct": all(c == "127.0.0.1" for c in SEEN["conns"]),
             "resolver_calls": resolver.calls, "aiohttp": aiohttp.__version__,
             "pass": wire_ae == ["identity"] and all(c == "127.0.0.1" for c in SEEN["conns"])}  # direct: no proxy hop
    results.append(extra)
    print(json.dumps(extra), flush=True)
    # 1 and 3 must not have opened a TCP connection at all
    ok = all(r["pass"] for r in results) and results[1]["server_connections"] == 0 \
        and results[3]["server_connections"] == 0 and results[5]["rss_growth_kib"] < 16 * 1024
    await sess.close()
    srv.close()
    srv2.close()
    print(json.dumps({"all_pass": ok, "maxrss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
