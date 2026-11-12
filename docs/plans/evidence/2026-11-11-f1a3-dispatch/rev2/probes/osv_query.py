"""Query api.osv.dev for known advisories per (package, version). Output: one JSON per line."""
import json
import urllib.request

PKGS = [("aiohttp","3.12.15"),("aiohttp","3.13.3"),("aiohttp","3.13.5"),("aiohttp","3.14.4"),("aiohttp","3.14.5"),
        ("httpx","0.28.1"),("httpcore","1.0.9"),("h11","0.16.0"),("cryptography","46.0.3"),("cryptography","50.0.2"),("uvloop","0.23.0")]
def q(name, ver):
    req = urllib.request.Request("https://api.osv.dev/v1/query", data=json.dumps(
        {"package": {"name": name, "ecosystem": "PyPI"}, "version": ver}).encode(),
        headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r).get("vulns", [])
for name, ver in PKGS:
    vulns = q(name, ver)
    ghsa = sorted({v["id"] for v in vulns if v["id"].startswith("GHSA")} |
                  {a for v in vulns for a in v.get("aliases", []) if a.startswith("GHSA")})
    print(json.dumps({"package": name, "version": ver, "osv_entries": len(vulns), "ghsa": ghsa}))
