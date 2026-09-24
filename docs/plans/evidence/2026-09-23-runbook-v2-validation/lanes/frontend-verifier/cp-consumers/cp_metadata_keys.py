#!/usr/bin/env python3
"""Static (read-only) quantification of gateway -> control-plane telemetry key coupling.

Answers:
 (a) distinct EnforcementEvent.metadata keys READ by control-plane non-test code
     (ORM/SQL lookups + python dict reads), split top-level vs nested (extra.*, ...)
 (b) keys WRITTEN by control core/tasks.py::_build_enforcement_metadata
 (c) top-level keys of gateway telemetry.py::build_telemetry_event
 (d) diff of the control vs workers _build_enforcement_metadata key sets
 plus cross-checks (read-but-never-written, written-but-never-read) and the raw
 gateway `metadata.*` keys the ingest path (builder/drain) consumes.

Heuristics (documented, reviewed by hand in the report):
 * ORM: any string / kwarg containing `metadata__<key>` (Q(), filter(), values(),
   exclude(), ...), KeyTransform/KeyTextTransform(<key>, "metadata"|KeyTransform("extra","metadata")),
   and call sites of the repo's key helpers (metadata_text, metadata_json,
   annotate_numeric_float, _json_key, _annotate_meta, _meta_from_row,
   metadata_matched_ids) with constant keys / constant key tuples.
 * Python reads: .get(<const>) / [<const>] / `<const> in X` on a receiver that is
   (transitively) derived from `<x>.metadata`, getattr(x, "metadata"), `.get("metadata")`,
   values_list("metadata"), or a function parameter named like metadata
   (meta, md, metadata, base_meta, ...). Receivers rooted at non-EnforcementEvent
   objects (agent/endpoint/policy/obj/self/log/...) are excluded.
 * Functions that BUILD EnforcementEvent metadata from raw gateway input (drain,
   builder, MCP record path, ingestion, evaluation write paths) are reported
   separately as "ingest-path reads" (they read RAW gateway keys, not stored EE metadata).
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
from collections import defaultdict

REPO = "/home/contact_cyberultron_com/AI_Mesh_Firewall"
CONTROL = os.path.join(REPO, "control/ai_mesh_control")
WORKERS = os.path.join(REPO, "workers/ai_mesh_workers")
GW_TELEMETRY = os.path.join(REPO, "gateway/ai_mesh_gateway/telemetry.py")
CTRL_TASKS = os.path.join(CONTROL, "core/tasks.py")
WK_TASKS = os.path.join(WORKERS, "tasks/telemetry.py")
OUT = os.path.dirname(os.path.abspath(__file__))

PARAM_SEEDS = {"meta", "md", "metadata", "base_meta", "ev_meta", "event_meta", "enforcement_metadata"}
NON_EE_ROOTS = {
    "policy", "self", "obj", "existing", "log", "agent", "ag", "ep", "endpoint", "Agent",
    "KillSwitchAuditLog", "instance", "cfg", "srv", "server", "profile", "org", "organization",
    "request", "tool", "reg", "registration",
}
PASS_THROUGH_FUNCS = {
    "dict", "copy", "deepcopy", "_enrich_scan_detail_metadata", "_merge_related_scan_metadata",
    "_sanitized_meta_for_client", "_meta_bucket", "_strip_nul",
}
# (function name) -> list of (arg selector, path) ; selector = ("pos", i) or ("kw", name)
HELPER_KEY_ARGS = {
    "metadata_text": [(("pos", 0), "")],
    "metadata_json": [(("pos", 0), "")],
    "annotate_numeric_float": [(("pos", 1), "")],
    "metadata_matched_ids": [(("pos", 1), "")],
    "_json_key": [(("pos", 0), "")],  # path becomes "extra" when 2nd arg == "extra" (handled below)
}
HELPER_KEY_SEQ_ARGS = {  # args that are sequences of keys
    "_annotate_meta": [(("pos", 1), ""), (("kw", "extra_keys"), "extra")],
    "_meta_from_row": [(("pos", 1), ""), (("kw", "extra_keys"), "extra")],
}
DJANGO_LOOKUPS = {
    "isnull", "exact", "iexact", "contains", "icontains", "in", "gt", "gte", "lt", "lte",
    "startswith", "istartswith", "endswith", "iendswith", "regex", "iregex", "has_key",
    "has_keys", "has_any_keys", "contained_by", "range", "keys", "values", "len",
}
# Functions that build EE metadata from RAW input (reads there are raw gateway/payload keys).
INGEST_PATH_FUNCS = {
    ("control/ai_mesh_control/core/tasks.py", "_build_enforcement_metadata"),
    ("control/ai_mesh_control/core/tasks.py", "drain_telemetry_from_redis"),
    ("workers/ai_mesh_workers/tasks/telemetry.py", "_build_enforcement_metadata"),
    ("workers/ai_mesh_workers/tasks/telemetry.py", "drain_telemetry_from_redis"),
    ("control/ai_mesh_control/policy/telemetry_resolution.py", "resolve_policy_rule_from_event"),
    ("control/ai_mesh_control/mcp_connector/views.py", "_record_event"),
    ("control/ai_mesh_control/mcp_connector/views.py", "post"),  # refined below by class
    ("control/ai_mesh_control/core/isolation_notify.py", "notify_isolation_from_control"),
}
INGEST_PATH_FILES = {
    "control/ai_mesh_control/core/ingestion_views.py",
    "control/ai_mesh_control/policy/evaluation_views.py",
    "control/ai_mesh_control/mcp_connector/tasks.py",
    "workers/ai_mesh_workers/tasks/mcp.py",
}
FACT_READ_FUNCS = {  # read AnalyticsHourlyGroupFact.meta (a projection of EE keys + derived is_critical)
    ("control/ai_mesh_control/policy/analytics_rollup.py", "_is_critical_meta"),
    ("control/ai_mesh_control/policy/analytics_rollup.py", "_accumulate_module"),
}
INGEST_PATH_CLASSES = {("control/ai_mesh_control/mcp_connector/views.py", "MCPGatewayRecordEventView")}

SEG = r"(?:[A-Za-z0-9]|_(?!_))+"
ORM_RE = re.compile(r"(?:^|__)metadata__(" + SEG + r")((?:__" + SEG + r")*)")


def rel(p):
    return os.path.relpath(p, REPO)


def is_test_path(p):
    parts = p.split(os.sep)
    base = os.path.basename(p)
    return ("tests" in parts or "migrations" in parts or base.startswith("test_")
            or base.endswith("_test.py") or base == "conftest.py")


def iter_py(root):
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in ("__pycache__", "tests", "migrations", "node_modules")]
        for f in fn:
            if f.endswith(".py"):
                p = os.path.join(dp, f)
                if not is_test_path(p):
                    yield p


def const_str_seq(node):
    """Return list of str constants if node is a tuple/list/set/frozenset(...) of str constants."""
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        vals = [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        if len(vals) == len(node.elts):
            return vals
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("frozenset", "set", "tuple", "list") and node.args:
        return const_str_seq(node.args[0])
    return None


def build_const_table(files):
    table = {}
    for p in files:
        try:
            tree = ast.parse(open(p, encoding="utf-8").read(), p)
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                seq = const_str_seq(node.value)
                if seq is not None:
                    table.setdefault(node.targets[0].id, seq)
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                seq = const_str_seq(node.value)
                if seq is not None:
                    table.setdefault(node.target.id, seq)
    return table


def root_name(node):
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
        if isinstance(node, ast.Call):
            node = node.func
        else:
            node = node.value
    return node.id if isinstance(node, ast.Name) else None


def join(path, key):
    return key if not path else f"{path}.{key}"


class Scope:
    def __init__(self, fn_node, const_table):
        self.fn = fn_node
        self.const = const_table
        self.tainted = {}
        # params (incl. nested defs / lambdas)
        for n in ast.walk(fn_node):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                for a in list(n.args.args) + list(n.args.kwonlyargs) + list(n.args.posonlyargs):
                    if a.arg in PARAM_SEEDS:
                        self.tainted[a.arg] = ""
        # parent map for per-usage loop-variable resolution
        self.parent = {}
        for p_ in ast.walk(fn_node):
            for c_ in ast.iter_child_nodes(p_):
                self.parent[c_] = p_
        # loop vars iterating values_list("metadata", ...) are tainted
        for n in ast.walk(fn_node):
            if isinstance(n, (ast.For, ast.comprehension)) and isinstance(n.target, ast.Name):
                src = ast.unparse(n.iter)
                if re.search(r"values(_list)?\([^)]*[\"']metadata[\"']", src):
                    self.tainted[n.target.id] = ""
        # fixpoint on assignments
        for _ in range(6):
            changed = False
            for n in ast.walk(fn_node):
                if isinstance(n, ast.Assign):
                    targets = [t for t in n.targets if isinstance(t, ast.Name)]
                    val = n.value
                elif isinstance(n, (ast.AnnAssign, ast.NamedExpr)):
                    targets = [n.target] if isinstance(n.target, ast.Name) else []
                    val = n.value
                else:
                    continue
                if val is None or not targets:
                    continue
                p = self.expr_path(val)
                if p is None:
                    continue
                for t in targets:
                    if self.tainted.get(t.id) != p and t.id not in self.tainted:
                        self.tainted[t.id] = p
                        changed = True
            if not changed:
                break

    def _seq_of(self, it):
        seq = const_str_seq(it)
        if seq is None and isinstance(it, ast.Name):
            seq = self.const.get(it.id)
        if seq is None and isinstance(it, ast.BinOp) and isinstance(it.op, ast.Add):
            l = const_str_seq(it.left) or (self.const.get(it.left.id) if isinstance(it.left, ast.Name) else None)
            r = const_str_seq(it.right) or (self.const.get(it.right.id) if isinstance(it.right, ast.Name) else None)
            if l is not None and r is not None:
                seq = l + r
        return seq

    def key_values(self, node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.Name):
            cur = self.parent.get(node)
            while cur is not None:
                if isinstance(cur, ast.For) and isinstance(cur.target, ast.Name) and cur.target.id == node.id:
                    return self._seq_of(cur.iter)
                if isinstance(cur, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
                    for g in cur.generators:
                        if isinstance(g.target, ast.Name) and g.target.id == node.id:
                            return self._seq_of(g.iter)
                cur = self.parent.get(cur)
        return None

    def expr_path(self, e):
        if isinstance(e, ast.BoolOp):
            for v in e.values:
                p = self.expr_path(v)
                if p is not None:
                    return p
            return None
        if isinstance(e, ast.IfExp):
            p = self.expr_path(e.body)
            return p if p is not None else self.expr_path(e.orelse)
        if isinstance(e, ast.Attribute) and e.attr == "metadata":
            r = root_name(e)
            if r in NON_EE_ROOTS:
                return None
            return ""
        if isinstance(e, ast.Name):
            return self.tainted.get(e.id)
        if isinstance(e, ast.Call):
            f = e.func
            fname = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
            if fname == "getattr" and len(e.args) >= 2 and isinstance(e.args[1], ast.Constant) and e.args[1].value == "metadata":
                return None if root_name(e.args[0]) in NON_EE_ROOTS else ""
            if isinstance(f, ast.Attribute) and f.attr == "get" and e.args:
                keys = self.key_values(e.args[0])
                if keys and keys == ["metadata"]:
                    return None if root_name(f.value) in NON_EE_ROOTS else ""
                base = self.expr_path(f.value)
                if base is not None and keys and len(keys) == 1:
                    return join(base, keys[0])
                return None
            if fname in PASS_THROUGH_FUNCS and e.args:
                return self.expr_path(e.args[0])
            return None
        if isinstance(e, ast.Subscript):
            base = self.expr_path(e.value)
            keys = self.key_values(e.slice)
            if base is not None and keys and len(keys) == 1:
                return join(base, keys[0])
            return None
        if isinstance(e, ast.Dict):
            # {**meta, ...} spread of a tainted dict
            for k, v in zip(e.keys, e.values):
                if k is None:
                    p = self.expr_path(v)
                    if p is not None:
                        return p
        return None


def enclosing_map(tree):
    """Map every node -> (class name, function name) of its innermost enclosing def."""
    owner = {}

    def visit(node, cls, fn):
        for child in ast.iter_child_nodes(node):
            c, f = cls, fn
            if isinstance(child, ast.ClassDef):
                c = child.name
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                f = child.name if fn is None else fn  # attribute nested defs to outermost def
            owner[child] = (c, f)
            visit(child, c, f)

    visit(tree, None, None)
    return owner


def analyze_file(path, const_table, hits):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src, path)
    rp = rel(path)
    owner = enclosing_map(tree)

    def category(node):
        cls, fn = owner.get(node, (None, None))
        if rp in INGEST_PATH_FILES:
            return "ingest"
        if (rp, cls) in INGEST_PATH_CLASSES:
            return "ingest"
        if (rp, fn) in INGEST_PATH_FUNCS and fn != "post":
            return "ingest"
        if (rp, fn) in FACT_READ_FUNCS:
            return "fact_read"
        return "ee_read"

    def add(kind, node, path_, key, note=""):
        cls, fn = owner.get(node, (None, None))
        hits.append({
            "kind": kind, "file": rp, "line": getattr(node, "lineno", 0),
            "class": cls or "", "func": fn or "<module>", "path": path_, "key": key,
            "cat": category(node), "note": note,
        })

    # ---- ORM string lookups (constants + kwarg names) --------------------------
    for node in ast.walk(tree):
        strings = []
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings.append((node, node.value, None))
        if isinstance(node, ast.keyword) and node.arg:
            strings.append((node, node.arg, node.value))
        if isinstance(node, ast.JoinedStr):
            s = "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
            if "metadata__" in s:
                add("orm_dynamic_fstring", node, "", "<dynamic>", ast.unparse(node)[:80])
            continue
        for n, s, kwval in strings:
            if "metadata__" not in s or " " in s:
                continue
            for m in ORM_RE.finditer(s):
                first = m.group(1)
                rest = [x for x in m.group(2).split("__") if x]
                if first in DJANGO_LOOKUPS:
                    if first in ("has_key",) and kwval is not None and isinstance(kwval, ast.Constant):
                        add("orm_has_key", n, "", str(kwval.value))
                    continue
                path_ = ""
                key = first
                if first == "extra" and rest and rest[0] not in DJANGO_LOOKUPS:
                    add("orm_lookup", n, "extra", rest[0], s)
                add("orm_lookup", n, path_, key, s)

    # ---- KeyTransform / KeyTextTransform + helper call sites -------------------
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        fname = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
        # need a scope for loop var resolution (comprehension over constant tuples)
        cls, fn = owner.get(node, (None, None))
        scope = None
        if fname in ("KeyTransform", "KeyTextTransform") and len(node.args) >= 2:
            srcn = node.args[1]
            if isinstance(srcn, ast.Constant) and srcn.value == "metadata":
                path_ = ""
            elif (isinstance(srcn, ast.Call) and getattr(srcn.func, "id", getattr(srcn.func, "attr", "")) == "KeyTransform"
                  and len(srcn.args) >= 2 and isinstance(srcn.args[0], ast.Constant)
                  and isinstance(srcn.args[1], ast.Constant) and srcn.args[1].value == "metadata"):
                path_ = srcn.args[0].value
            else:
                continue  # KeyTransform over something else (dynamic source); helper call sites cover it
            keys = None
            k = node.args[0]
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                keys = [k.value]
            else:
                scope = scope or Scope(find_def(tree, node, owner) or tree, const_table)
                keys = scope.key_values(k)
            if keys is None:
                add("keytransform_dynamic", node, path_, "<param>", ast.unparse(node)[:80])
                continue
            for kk in keys:
                if path_:
                    add("keytransform", node, "", path_)
                add("keytransform", node, path_, kk)
        if fname in HELPER_KEY_ARGS:
            for (sel, path_) in HELPER_KEY_ARGS[fname]:
                arg = pick(node, sel)
                if arg is None:
                    continue
                scope = scope or Scope(find_def(tree, node, owner) or tree, const_table)
                keys = scope.key_values(arg)
                if keys is None:
                    if fname in ("metadata_text", "metadata_json", "_json_key", "metadata_matched_ids") and isinstance(arg, ast.Name):
                        continue  # helper body / param forwarding; constant call sites are counted
                    add("helper_dynamic", node, path_, "<param>", ast.unparse(node)[:80])
                    continue
                if fname == "_json_key" and len(node.args) > 1 and isinstance(node.args[1], ast.Constant) and node.args[1].value == "extra":
                    path_ = "extra"
                    add("helper:" + fname, node, "", "extra")
                for kk in keys:
                    add("helper:" + fname, node, path_, kk)
        if fname in HELPER_KEY_SEQ_ARGS:
            for (sel, path_) in HELPER_KEY_SEQ_ARGS[fname]:
                arg = pick(node, sel)
                if arg is None:
                    continue
                seq = const_str_seq(arg) or (const_table.get(arg.id) if isinstance(arg, ast.Name) else None)
                if seq is None:
                    continue
                for kk in seq:
                    if path_:
                        add("helper:" + fname, node, "", path_)
                    add("helper:" + fname, node, path_, kk)
            if fname == "_annotate_meta":
                for kw in node.keywords:
                    if kw.arg == "risk" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        add("helper:_annotate_meta", node, "", "security_risk_score")

    # ---- python dict reads on tainted receivers --------------------------------
    for fn_node in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        scope = Scope(fn_node, const_table)
        for node in ast.walk(fn_node):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args:
                base = scope.expr_path(node.func.value)
                if base is None:
                    continue
                keys = scope.key_values(node.args[0])
                if keys is None:
                    continue
                for kk in keys:
                    if kk == "metadata" and base == "":
                        continue
                    add("py_get", node, base, kk)
            elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
                base = scope.expr_path(node.value)
                if base is None:
                    continue
                keys = scope.key_values(node.slice)
                if keys is None:
                    continue
                for kk in keys:
                    add("py_subscript", node, base, kk)
            elif isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], (ast.In, ast.NotIn)):
                base = scope.expr_path(node.comparators[0])
                if base not in ("", "extra", "pipeline_trace", "extra.pipeline_trace"):
                    continue
                keys = scope.key_values(node.left)
                if keys is None:
                    continue
                for kk in keys:
                    add("py_in", node, base, kk)


def pick(call, sel):
    kind, v = sel
    if kind == "pos":
        return call.args[v] if len(call.args) > v else None
    for kw in call.keywords:
        if kw.arg == v:
            return kw.value
    return None


def find_def(tree, node, owner):
    cls, fn = owner.get(node, (None, None))
    if fn is None:
        return None
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn:
            for sub in ast.walk(n):
                if sub is node:
                    return n
    return None


# ------------------------------------------------------------------------------
def builder_keys(path, fname="_build_enforcement_metadata"):
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == fname)
    always, conditional = set(), set()
    parents = {}
    for p in ast.walk(fn):
        for c in ast.iter_child_nodes(p):
            parents[c] = p

    def is_conditional(n):
        cur = parents.get(n)
        while cur is not None and cur is not fn:
            if isinstance(cur, (ast.If, ast.For, ast.While, ast.Try, ast.With)):
                return True
            cur = parents.get(cur)
        return False

    loopvars = {}
    for n in ast.walk(fn):
        if isinstance(n, ast.For) and isinstance(n.target, ast.Name):
            seq = const_str_seq(n.iter)
            if seq:
                loopvars[n.target.id] = seq
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "result" and isinstance(n.value, ast.Dict):
                    for k in n.value.keys:
                        if isinstance(k, ast.Constant):
                            (conditional if is_conditional(n) else always).add(k.value)
                if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id == "result":
                    sl = t.slice
                    keys = [sl.value] if isinstance(sl, ast.Constant) else loopvars.get(getattr(sl, "id", ""), [])
                    for k in keys:
                        (conditional if is_conditional(n) else always).add(k)
    return always, conditional - always


def telemetry_event_keys(path):
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "build_telemetry_event")
    ret = [n for n in ast.walk(fn) if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict)]
    keys = [k.value for k in ret[-1].value.keys if isinstance(k, ast.Constant)]
    return keys, fn.lineno, ret[-1].lineno


def dict_literal_keys(path, func, var):
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == func:
            for n in ast.walk(fn):
                if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == var for t in n.targets) and isinstance(n.value, ast.Dict):
                    return {k.value for k in n.value.keys if isinstance(k, ast.Constant)}, n.lineno
    return set(), 0


def raw_receiver_keys(path, func, receivers):
    """Constant keys read via <recv>.get(k) / <recv>[k] / `k in <recv>` inside `func`,
    resolving loop vars over constant tuples (for model_routed extras / owasp scan blocks)."""
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    fn = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func)
    loopvars = {}
    for n in ast.walk(fn):
        if isinstance(n, ast.For) and isinstance(n.target, ast.Name):
            seq = const_str_seq(n.iter)
            if seq:
                loopvars[n.target.id] = seq
    keys = set()

    def kv(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.Name):
            return loopvars.get(node.id, [])
        return []

    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get" \
                and isinstance(n.func.value, ast.Name) and n.func.value.id in receivers and n.args:
            keys.update(kv(n.args[0]))
        if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and n.value.id in receivers:
            keys.update(kv(n.slice))
        if isinstance(n, ast.Compare) and len(n.ops) == 1 and isinstance(n.ops[0], ast.In) \
                and isinstance(n.comparators[0], ast.Name) and n.comparators[0].id in receivers:
            keys.update(kv(n.left))
    return keys


def main():
    files_ctrl = sorted(iter_py(CONTROL))
    files_wk = sorted(iter_py(WORKERS))
    const_table = build_const_table(files_ctrl + files_wk)
    hits = []
    for p in files_ctrl + files_wk:
        try:
            analyze_file(p, const_table, hits)
        except SyntaxError as exc:
            print("SKIP (syntax)", p, exc, file=sys.stderr)

    # dedupe
    seen, uniq = set(), []
    for h in hits:
        k = (h["file"], h["line"], h["path"], h["key"], h["kind"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(h)
    hits = uniq

    with open(os.path.join(OUT, "reads.tsv"), "w") as fh:
        fh.write("cat\tkind\tfile:line\tclass\tfunc\tpath\tkey\tnote\n")
        for h in sorted(hits, key=lambda x: (x["cat"], x["file"], x["line"], x["key"])):
            fh.write(f'{h["cat"]}\t{h["kind"]}\t{h["file"]}:{h["line"]}\t{h["class"]}\t{h["func"]}\t{h["path"]}\t{h["key"]}\t{h["note"]}\n')

    def keyset(pred):
        top, nested = set(), set()
        for h in hits:
            if not pred(h) or h["key"].startswith("<"):
                continue
            if h["path"] == "":
                top.add(h["key"])
            else:
                nested.add(f'{h["path"]}.{h["key"]}')
        return top, nested

    ee = lambda h: h["cat"] == "ee_read"
    ctrl_only = lambda h: h["cat"] == "ee_read" and h["file"].startswith("control/")
    orm = lambda h: h["cat"] == "ee_read" and (h["kind"].startswith("orm") or h["kind"].startswith("keytransform") or h["kind"].startswith("helper"))
    py = lambda h: h["cat"] == "ee_read" and h["kind"].startswith("py_")
    ingest = lambda h: h["cat"] == "ingest"

    ee_top, ee_nested = keyset(ee)
    ctrl_top, ctrl_nested = keyset(ctrl_only)
    orm_top, orm_nested = keyset(orm)
    py_top, py_nested = keyset(py)
    ing_top, ing_nested = keyset(ingest)

    b_always, b_cond = builder_keys(CTRL_TASKS)
    w_always, w_cond = builder_keys(WK_TASKS)
    b_all, w_all = b_always | b_cond, w_always | w_cond
    tel_keys, tel_def_line, tel_ret_line = telemetry_event_keys(GW_TELEMETRY)

    mcp_mirror, mcp_line = dict_literal_keys(os.path.join(CONTROL, "mcp_connector/views.py"), "_record_event", "_ef_metadata")
    iso_keys, iso_line = dict_literal_keys(os.path.join(CONTROL, "core/isolation_notify.py"), "notify_isolation_from_control", "meta")
    bf_keys, bf_line = dict_literal_keys(os.path.join(CONTROL, "mcp_connector/management/commands/backfill_mcp_events.py"), "handle", "metadata")

    other_writers = mcp_mirror | iso_keys | bf_keys
    read_not_built = sorted(ee_top - b_all)
    read_not_any = sorted(ee_top - b_all - other_writers)
    built_not_read = sorted(b_all - ee_top)

    # per-key provenance (first 3 sites) for EE reads
    prov = defaultdict(list)
    for h in sorted(hits, key=lambda x: (x["file"], x["line"])):
        if h["cat"] != "ee_read" or h["key"].startswith("<"):
            continue
        k = h["key"] if h["path"] == "" else f'{h["path"]}.{h["key"]}'
        site = f'{h["file"].replace("control/ai_mesh_control/", "").replace("workers/ai_mesh_workers/", "workers:")}:{h["line"]}'
        if site not in prov[k]:
            prov[k].append(site)

    files_per_key = {k: len({s.rsplit(":", 1)[0] for s in v}) for k, v in prov.items()}

    summary = {
        "a_ee_metadata_keys_read_top_level": sorted(ee_top),
        "a_count_top_level": len(ee_top),
        "a_nested_keys_read": sorted(ee_nested),
        "a_count_nested": len(ee_nested),
        "a_control_only_top_level_count": len(ctrl_top),
        "a_orm_sql_top_level": sorted(orm_top), "a_orm_sql_count": len(orm_top),
        "a_python_dict_top_level": sorted(py_top), "a_python_count": len(py_top),
        "a_orm_nested": sorted(orm_nested), "a_py_nested": sorted(py_nested),
        "ingest_path_raw_keys_top": sorted(ing_top), "ingest_path_raw_keys_nested": sorted(ing_nested),
        "b_builder_control_keys_always": sorted(b_always), "b_builder_control_keys_conditional": sorted(b_cond),
        "b_count": len(b_all), "b_count_always": len(b_always), "b_count_conditional": len(b_cond),
        "c_build_telemetry_event_keys": tel_keys, "c_count": len(tel_keys),
        "c_def_line": tel_def_line, "c_return_line": tel_ret_line,
        "d_workers_builder_keys": sorted(w_all), "d_workers_count": len(w_all),
        "d_only_in_control_builder": sorted(b_all - w_all), "d_only_in_workers_builder": sorted(w_all - b_all),
        "d_identical": b_all == w_all,
        "other_writers": {
            f"mcp_connector/views.py:{mcp_line} _record_event._ef_metadata": sorted(mcp_mirror),
            f"core/isolation_notify.py:{iso_line} notify_isolation_from_control.meta": sorted(iso_keys),
            f"mcp_connector/management/commands/backfill_mcp_events.py:{bf_line} metadata": sorted(bf_keys),
        },
        "x_read_top_level_not_written_by_drain_builder": read_not_built,
        "x_read_top_level_not_written_by_any_literal_writer": read_not_any,
        "x_written_by_builder_never_read_by_control_code": built_not_read,
        "provenance_first_sites": {k: v[:4] for k, v in sorted(prov.items())},
        "files_per_key": dict(sorted(files_per_key.items(), key=lambda kv: -kv[1])),
        "n_hits_total": len(hits),
        "n_files_scanned": len(files_ctrl) + len(files_wk),
    }
    # gateway metadata.* keys consumed by the CONTROL drain (script hits + model_routed extras + shared OWASP resolver)
    drain_hits = {h["key"] for h in hits if h["cat"] == "ingest" and h["file"] == "control/ai_mesh_control/core/tasks.py" and not h["path"] and not h["key"].startswith("<")}
    routed = raw_receiver_keys(CTRL_TASKS, "_build_enforcement_metadata", {"extra"})
    owasp = raw_receiver_keys(os.path.join(REPO, "shared/ai_mesh_shared/owasp_telemetry.py"), "resolve_owasp_codes", {"extra"})
    drain_raw = drain_hits | routed | owasp
    wk_drain_hits = {h["key"] for h in hits if h["cat"] == "ingest" and h["file"] == "workers/ai_mesh_workers/tasks/telemetry.py" and not h["path"] and not h["key"].startswith("<")}
    wk_routed = raw_receiver_keys(WK_TASKS, "_build_enforcement_metadata", {"extra"})
    wk_drain_raw = wk_drain_hits | wk_routed | owasp
    summary["e_gateway_metadata_keys_consumed_by_control_drain"] = sorted(drain_raw)
    summary["e_count"] = len(drain_raw)
    summary["e_parts"] = {"drain_builder_hits": sorted(drain_hits), "model_routed_extra": sorted(routed), "shared_resolve_owasp_codes": sorted(owasp)}
    summary["e_workers_drain_count"] = len(wk_drain_raw)
    summary["e_only_control_drain"] = sorted(drain_raw - wk_drain_raw)
    summary["e_only_workers_drain"] = sorted(wk_drain_raw - drain_raw)
    json.dump(summary, open(os.path.join(OUT, "summary.json"), "w"), indent=1)

    lines = []
    P = lines.append
    P(f"files scanned (non-test, non-migration): control={len(files_ctrl)} workers={len(files_wk)}; read hits={len(hits)}")
    P("")
    P(f"(a) EnforcementEvent.metadata TOP-LEVEL keys read (control+workers, EE-reader code): {len(ee_top)}")
    P("    " + ", ".join(sorted(ee_top)))
    P(f"    of which via ORM/SQL (metadata__/KeyTransform/helpers): {len(orm_top)} ; via python dict reads: {len(py_top)} ; control-only: {len(ctrl_top)}")
    P(f"    nested keys read (under metadata.<x>.): {len(ee_nested)}")
    P("    " + ", ".join(sorted(ee_nested)))
    P("")
    P(f"(a') RAW gateway-event keys read on the ingest path (builder/drain/MCP-record/ingestion/evaluation): top={len(ing_top)} nested={len(ing_nested)}")
    P("    top: " + ", ".join(sorted(ing_top)))
    P("")
    P(f"(b) control core/tasks.py::_build_enforcement_metadata WRITES {len(b_all)} keys ({len(b_always)} always + {len(b_cond)} conditional)")
    P("    always: " + ", ".join(sorted(b_always)))
    P("    conditional: " + ", ".join(sorted(b_cond)))
    P("")
    P(f"(c) gateway telemetry.py::build_telemetry_event (def L{tel_def_line}, return L{tel_ret_line}) top-level keys: {len(tel_keys)}")
    P("    " + ", ".join(tel_keys))
    P("")
    P(f"(d) workers tasks/telemetry.py::_build_enforcement_metadata writes {len(w_all)} keys; identical to control? {b_all == w_all}")
    P("    only in CONTROL builder: " + ", ".join(sorted(b_all - w_all)))
    P("    only in WORKERS builder: " + ", ".join(sorted(w_all - b_all)))
    P("")
    P(f"(x1) top-level keys READ but NOT written by the drain builder: {len(read_not_built)}")
    P("    " + ", ".join(read_not_built))
    P(f"(x2) ...and not written by any literal EE writer (builder, MCP mirror, isolation_notify, backfill): {len(read_not_any)}")
    P("    " + ", ".join(read_not_any))
    P(f"(x3) builder-written keys never read by control code (reach UI only via threat-feed metadata pass-through): {len(built_not_read)}")
    P("    " + ", ".join(built_not_read))
    P("")
    P(f"(e) gateway `metadata.*` keys consumed by CONTROL drain/builder: {len(drain_raw)} (builder/drain hits {len(drain_hits)} + model_routed extras {len(routed)} + shared resolve_owasp_codes {len(owasp)}, overlap removed)")
    P("    " + ", ".join(sorted(drain_raw)))
    P(f"    workers drain copy consumes {len(wk_drain_raw)}; only-control: {', '.join(sorted(drain_raw - wk_drain_raw))}; only-workers: {', '.join(sorted(wk_drain_raw - drain_raw))}")
    P("")
    P("per-key provenance (first sites) -> see summary.json['provenance_first_sites']; all hits -> reads.tsv")
    open(os.path.join(OUT, "report.txt"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
