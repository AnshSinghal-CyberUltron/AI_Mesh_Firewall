#!/usr/bin/env python3
"""Env-var names DECLARED in deploy/config files (compose environment keys, KEY=VALUE env files,
terraform map keys, Dockerfile ENV, shell export/_set_default) and names INTERPOLATED (${VAR}).
Comment lines are reported separately. Usage: config_env_decls.py <root> <regex-filter>"""
import os, re, sys, json
root, filt = sys.argv[1], re.compile(sys.argv[2])
EXT = (".yml", ".yaml", ".env", ".sh", ".tf", ".tfvars", ".sample", ".example", ".ini", ".conf")
decl_re = [re.compile(r"^\s*(?:-\s*)?([A-Z][A-Z0-9_]+)\s*[:=]"),          # compose map / KEY=V / tf map / list form
           re.compile(r"^\s*export\s+([A-Z][A-Z0-9_]+)="),
           re.compile(r"^\s*ENV\s+([A-Z][A-Z0-9_]+)[\s=]"),
           re.compile(r"_set_default\s+([A-Z][A-Z0-9_]+)")]
interp_re = re.compile(r"\$\{([A-Z][A-Z0-9_]+)")
res = {"declared": {}, "declared_commented": {}, "interpolated": {}}
for dp, dn, fn in os.walk(root):
    dn[:] = [d for d in dn if d not in ("node_modules", ".git", "__pycache__")]
    for f in fn:
        if not (f.endswith(EXT) or f.startswith(".env") or f.startswith("Dockerfile")):
            continue
        p = os.path.join(dp, f); rel = os.path.relpath(p, root)
        for i, line in enumerate(open(p, encoding="utf-8", errors="replace"), 1):
            s = line.strip()
            commented = s.startswith("#")
            body = s.lstrip("#").strip() if commented else line
            for rx in decl_re:
                m = rx.search(body)
                if m and filt.search(m.group(1)):
                    key = "declared_commented" if commented else "declared"
                    res[key].setdefault(m.group(1), []).append(f"{rel}:{i}")
                    break
            if not commented:
                for m in interp_re.finditer(line):
                    if filt.search(m.group(1)):
                        res["interpolated"].setdefault(m.group(1), []).append(f"{rel}:{i}")
for k in res:
    res[k] = dict(sorted(res[k].items()))
print(json.dumps({k: {"n": len(v), "names": v} for k, v in res.items()}, indent=1))
