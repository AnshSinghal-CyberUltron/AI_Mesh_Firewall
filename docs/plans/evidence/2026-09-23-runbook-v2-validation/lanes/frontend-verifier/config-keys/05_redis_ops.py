#!/usr/bin/env python3
"""Task 3 step 2: AST inventory of Redis operation call sites.
control-plane (non-test): WRITE ops; gateway (non-test): READ ops (+ writes, for direction checks).
For ambiguous method names (get/set/delete/...) a call is kept only when the receiver or first
argument looks Redis-ish (receiver name contains redis/client/pipe/conn/rc/_r, or first arg is a
key-like string/f-string or an identifier containing key/KEY/prefix/PREFIX/channel/CHANNEL).
Read-only AST walk; prints file:line, op, receiver, first-arg source.
"""
import ast
import json
import os
import re
from collections import Counter, defaultdict

REPO = "/home/contact_cyberultron_com/AI_Mesh_Firewall"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOTS = {
    "control": f"{REPO}/control/ai_mesh_control",
    "gateway": f"{REPO}/gateway/ai_mesh_gateway",
    "shared": f"{REPO}/shared/ai_mesh_shared",
}
SKIP = {"tests", "__pycache__", "migrations", "graphify-out", ".venv", "node_modules"}
WRITE_OPS = {"set", "setex", "psetex", "setnx", "mset", "hset", "hmset", "hsetnx", "hincrby", "hdel",
             "sadd", "srem", "publish", "xadd", "lpush", "rpush", "zadd", "zrem", "incr", "incrby",
             "decr", "expire", "pexpire", "delete", "unlink", "getdel", "rename", "eval", "evalsha",
             "xtrim", "ltrim", "lrem", "getset"}
READ_OPS = {"get", "mget", "hget", "hgetall", "hmget", "hkeys", "hvals", "hexists", "smembers",
            "sismember", "scard", "subscribe", "psubscribe", "xread", "xreadgroup", "xrange",
            "xrevrange", "brpop", "blpop", "brpoplpush", "lrange", "lindex", "llen", "zrange",
            "zrangebyscore", "zscore", "scan_iter", "scan", "keys", "exists", "ttl", "type", "lpop", "rpop"}
AMBIG = {"get", "set", "delete", "keys", "type", "scan", "exists", "incr", "expire", "eval",
         "rename", "publish", "subscribe", "hget", "hset"}
REDISH_RECV = re.compile(r"(redis|client|pipe|conn|^rc$|^r$|_r$|^cli|pubsub|REDIS|_redis|^c$)", re.I)
KEYISH_ARG = re.compile(r"(key|KEY|prefix|PREFIX|channel|CHANNEL|STREAM|stream|QUEUE|queue)")


def py_files(root):
    for r, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith(".")]
        for f in files:
            if f.endswith(".py") and not f.startswith("test_") and f != "conftest.py":
                yield os.path.join(r, f)


def recv_name(node):
    v = node.func.value
    try:
        return ast.unparse(v)[:60]
    except Exception:
        return "?"


def keyish(arg):
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return ":" in arg.value or arg.value.isidentifier()
    if isinstance(arg, ast.JoinedStr):
        return any(isinstance(v, ast.Constant) and ":" in str(v.value) for v in arg.values)
    src = ast.unparse(arg)
    return bool(KEYISH_ARG.search(src))


rows = []
for side, root in ROOTS.items():
    for p in py_files(root):
        try:
            tree = ast.parse(open(p, encoding="utf-8", errors="replace").read())
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
                continue
            op = n.func.attr
            if op not in WRITE_OPS and op not in READ_OPS:
                continue
            r = recv_name(n)
            a0 = n.args[0] if n.args else None
            if op in AMBIG or op in ("get", "set"):
                ok = bool(REDISH_RECV.search(r.split(".")[-1])) or (a0 is not None and keyish(a0))
                # exclude obvious dict/obj .get("literal-without-colon") calls unless receiver is redis-ish
                if op == "get" and a0 is not None and isinstance(a0, ast.Constant) and isinstance(a0.value, str) and ":" not in a0.value and not REDISH_RECV.search(r.split(".")[-1]):
                    ok = False
                if op == "get" and a0 is not None and isinstance(a0, ast.Constant) and isinstance(a0.value, str) and ":" not in a0.value and r.split(".")[-1] in ("headers", "data", "body", "cfg", "config", "kwargs", "payload", "meta"):
                    ok = False
                if not ok:
                    continue
                # drop obvious non-redis receivers
                if re.search(r"(^|\.)(os\.environ|environ|request|headers|kwargs|dict|json|cache_dict|_cache|settings|body|data|payload|meta|result|resp|response|params|query_params|GET|POST|META|cfg|config|CONFIG|org_config|policy|entry|item|row|obj|d|m|x|v|e|s|t)$", r) and not REDISH_RECV.search(r.split(".")[-1]):
                    continue
            try:
                a0s = ast.unparse(a0)[:110] if a0 is not None else ""
            except Exception:
                a0s = "?"
            rows.append({"side": side, "loc": f"{os.path.relpath(p, REPO)}:{n.lineno}", "op": op,
                         "kind": "WRITE" if op in WRITE_OPS and op not in READ_OPS else ("READ" if op in READ_OPS and op not in WRITE_OPS else "BOTH"),
                         "recv": r, "arg0": a0s})

ctrl_w = [r for r in rows if r["side"] == "control" and r["kind"] in ("WRITE", "BOTH")]
gw_r = [r for r in rows if r["side"] == "gateway" and r["kind"] in ("READ", "BOTH")]
print(f"control candidate Redis WRITE call sites: {len(ctrl_w)}  ops={dict(Counter(r['op'] for r in ctrl_w))}")
print(f"gateway candidate Redis READ call sites: {len(gw_r)}  ops={dict(Counter(r['op'] for r in gw_r))}")
print("\n=== CONTROL WRITE call sites ===")
for r in sorted(ctrl_w, key=lambda r: r["loc"]):
    print(f"  {r['loc']:70s} {r['op']:9s} recv={r['recv']:28s} arg0={r['arg0']}")
print("\n=== GATEWAY READ call sites ===")
for r in sorted(gw_r, key=lambda r: r["loc"]):
    print(f"  {r['loc']:70s} {r['op']:9s} recv={r['recv']:28s} arg0={r['arg0']}")
print("\n=== CONTROL publish / GATEWAY subscribe (channels) ===")
for r in rows:
    if r["op"] in ("publish", "subscribe", "psubscribe"):
        print(f"  {r['side']:8s} {r['loc']:70s} {r['op']:10s} arg0={r['arg0']}")
json.dump(rows, open(os.path.join(HERE, "05_redis_ops.json"), "w"), indent=1)
