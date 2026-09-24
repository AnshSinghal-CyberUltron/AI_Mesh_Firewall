#!/usr/bin/env python3
"""Task 1(a)(b)(d): enumerate FirewallConfig model fields (AST), the serializer's
exposed fields (derived from Meta.exclude + declared fields), the gateway payload
mapping (build_gateway_payload AST), and grep the gateway non-test code for each key.

Read-only: parses source files; no Django setup, no DB, no Redis.
"""
import ast
import json
import os
import re
import subprocess
import sys

REPO = "/home/contact_cyberultron_com/AI_Mesh_Firewall"
MODELS = f"{REPO}/control/ai_mesh_control/core/models.py"
SER = f"{REPO}/control/ai_mesh_control/core/firewall_config_serializer.py"
GW = f"{REPO}/gateway/ai_mesh_gateway"
OUT = os.path.dirname(os.path.abspath(__file__))


def find_class(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise SystemExit(f"class {name} not found")


def field_call_name(call):
    f = call.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return "?"


# ---------------- (a) model fields ----------------
src = open(MODELS).read()
tree = ast.parse(src)
cls = find_class(tree, "FirewallConfig")
bases = [ast.unparse(b) for b in cls.bases]
model_fields = []  # (name, type, lineno)
for stmt in cls.body:
    if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call):
        fname = field_call_name(stmt.value)
        if fname.endswith("Field") or fname in ("ForeignKey", "OneToOneField", "ManyToManyField"):
            for t in stmt.targets:
                if isinstance(t, ast.Name):
                    model_fields.append((t.id, fname, stmt.lineno))

print(f"FirewallConfig class at {MODELS}:{cls.lineno}  bases={bases}")
print(f"(a) declared model fields: {len(model_fields)} (+ implicit auto 'id' => {len(model_fields)+1} concrete incl. id)")
for n, t, ln in model_fields:
    print(f"   {n:40s} {t:22s} models.py:{ln}")

# ---------------- (b) serializer fields ----------------
ssrc = open(SER).read()
stree = ast.parse(ssrc)
scls = find_class(stree, "FirewallConfigSerializer")
declared = []
exclude = None
read_only = None
for stmt in scls.body:
    if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call):
        for t in stmt.targets:
            if isinstance(t, ast.Name):
                declared.append((t.id, field_call_name(stmt.value), stmt.lineno))
    if isinstance(stmt, ast.ClassDef) and stmt.name == "Meta":
        for m in stmt.body:
            if isinstance(m, ast.Assign):
                tname = m.targets[0].id
                if tname == "exclude":
                    exclude = ast.literal_eval(m.value)
                    exclude_ln = m.lineno
                if tname == "read_only_fields":
                    read_only = ast.literal_eval(m.value)
                    ro_ln = m.lineno
print()
print(f"Serializer {SER}:{scls.lineno}  Meta.exclude={exclude} (line {exclude_ln})  read_only_fields={read_only} (line {ro_ln})")
print(f"   declared serializer fields: {[(d[0], d[1], d[2]) for d in declared]}")
model_names = [n for n, _, _ in model_fields]
ser_fields = [n for n in model_names if n not in exclude]
for d in declared:
    if d[0] not in ser_fields:
        ser_fields.append(d[0])
method_fields = [d[0] for d in declared if d[1] == "SerializerMethodField"]
writable = [f for f in ser_fields if f not in read_only and f not in method_fields]
print(f"(b) serializer-exposed fields: {len(ser_fields)} = {len(model_names)} model - {len([e for e in exclude if e in model_names])} excluded model fields ({[e for e in exclude if e in model_names]}) + {len(method_fields)} SerializerMethodFields {method_fields}")
print(f"    writable (not read_only, not method): {len(writable)}  read-only: {sorted(set(ser_fields)-set(writable))}")

# ---------------- payload mapping (build_gateway_payload) ----------------
fn = None
for stmt in cls.body:
    if isinstance(stmt, ast.FunctionDef) and stmt.name == "build_gateway_payload":
        fn = stmt
ret = [n for n in ast.walk(fn) if isinstance(n, ast.Return)][0]
payload_map = {}  # payload_key -> (model_field_or_expr, lineno)
field_to_payload = {}
for k, v in zip(ret.value.keys, ret.value.values):
    key = ast.literal_eval(k)
    attrs = {n.attr for n in ast.walk(v) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "self"}
    # locals: blocked_kw / allowed_mdl derive from self.blocked_keywords / self.allowed_models
    names = {n.id for n in ast.walk(v) if isinstance(n, ast.Name)}
    if "blocked_kw" in names:
        attrs.add("blocked_keywords")
    if "allowed_mdl" in names:
        attrs.add("allowed_models")
    payload_map[key] = (sorted(attrs), k.lineno)
    for a in attrs:
        field_to_payload.setdefault(a, []).append(key)
print()
print(f"build_gateway_payload at models.py:{fn.lineno}: {len(payload_map)} payload keys (+ 'org_slug' injected by signals.py)")
renamed = {f: ks for f, ks in field_to_payload.items() if ks != [f]}
print(f"   renamed field->payload keys: {renamed}")
not_in_payload = [f for f in ser_fields if f not in field_to_payload]
print(f"   serializer fields NOT emitted in payload ({len(not_in_payload)}): {not_in_payload}")


# ---------------- (d) gateway grep ----------------
def gw_files():
    out = []
    for root, dirs, files in os.walk(GW):
        dirs[:] = [d for d in dirs if d not in ("tests", "__pycache__", "graphify-out") and not d.startswith(".")]
        for f in files:
            if f.endswith(".py") and not f.startswith("test_") and f != "conftest.py":
                out.append(os.path.join(root, f))
    return sorted(out)


FILES = gw_files()
TEXT = {p: open(p, encoding="utf-8", errors="replace").read().splitlines() for p in FILES}


def grep_key(key):
    pat = re.compile(r"""(["'])""" + re.escape(key) + r"""\1""")
    hits = []
    for p, lines in TEXT.items():
        for i, line in enumerate(lines, 1):
            if pat.search(line):
                hits.append((os.path.relpath(p, REPO), i, line.strip()[:160]))
    return hits


# Hits that are only declarations (type tables in config_sync.py / env defaults in config.py)
def is_decl_only(hit):
    path, ln, line = hit
    if path.endswith("gateway/ai_mesh_gateway/config_sync.py") and ln <= 110:
        return True  # _BOOL_KEYS/_NUM_KEYS/_STR_KEYS/_LIST_KEYS/_NULLABLE_KEYS type tables
    return False


print()
print(f"(d) gateway non-test .py files scanned (recursive, excluding tests/, test_*.py, conftest.py): {len(FILES)}")
rows = []
for f in ser_fields:
    lit_hits = grep_key(f)
    pkeys = field_to_payload.get(f, [])
    pk_hits = {pk: grep_key(pk) for pk in pkeys}
    rows.append({
        "field": f,
        "literal_hits": len(lit_hits),
        "literal_nondecl_hits": len([h for h in lit_hits if not is_decl_only(h)]),
        "payload_keys": pkeys,
        "payload_key_hits": {pk: len(h) for pk, h in pk_hits.items()},
        "payload_key_nondecl_hits": {pk: len([x for x in h if not is_decl_only(x)]) for pk, h in pk_hits.items()},
        "literal_hit_sample": lit_hits[:6],
        "payload_hit_sample": {pk: h[:6] for pk, h in pk_hits.items()},
    })

lit_read = [r["field"] for r in rows if r["literal_hits"] > 0]
lit_not = [r["field"] for r in rows if r["literal_hits"] == 0]
print(f"   LITERAL serializer-field-name grep: READ={len(lit_read)}  NOT-READ={len(lit_not)}")
print(f"   NOT-READ (literal): {lit_not}")
# payload-aware: consumed if any payload key has non-declaration hits
cons = [r["field"] for r in rows if any(v > 0 for v in r["payload_key_nondecl_hits"].values())]
not_cons = [r["field"] for r in rows if r["field"] not in cons]
print(f"   PAYLOAD-AWARE (field -> emitted payload key, excluding config_sync.py type-table lines): CONSUMED={len(cons)} NOT-CONSUMED={len(not_cons)}")
print(f"   NOT-CONSUMED (payload-aware): {not_cons}")
print()
for r in rows:
    print(f" - {r['field']}: literal_hits={r['literal_hits']} (non-decl {r['literal_nondecl_hits']}); payload_keys={r['payload_keys']} hits={r['payload_key_hits']} non-decl={r['payload_key_nondecl_hits']}")
    for pk, hs in r["payload_hit_sample"].items():
        for h in hs[:4]:
            print(f"       [{pk}] {h[0]}:{h[1]}: {h[2]}")
    if not r["payload_keys"]:
        for h in r["literal_hit_sample"][:4]:
            print(f"       [literal] {h[0]}:{h[1]}: {h[2]}")

json.dump({"model_fields": model_fields, "serializer_fields": ser_fields, "payload_map": payload_map,
           "rows": rows, "gw_files": [os.path.relpath(p, REPO) for p in FILES]},
          open(os.path.join(OUT, "01_firewallconfig_fields.json"), "w"), indent=1, default=str)
