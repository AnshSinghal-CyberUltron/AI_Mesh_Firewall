"""Revision 7 (Plan Reviewer B, revision 6, should-fix 7): what a projector restart costs at 100k orgs.

    python check_plan7.py

Revision 6 kept its validation memo in projector memory, so a restart re-ran `check_plan` for every org
(B: 2.26 ms per 10-rule plan, 13.3 ms per 64-rule plan: 226-1,332 s at 100k orgs). Revision 7 skips
validation when the desired body's sha256 equals the body hash carried in the org's current signed PLAN
record (the verdict was computed when that record was written, and travels in it). Measured: check_plan
per plan (cold and warm caches) against the hash comparison, for plan bodies of the two sizes.
"""
import hashlib, json, re, statistics, sys, time, warnings
warnings.simplefilter("ignore")
sys.path.insert(0, ".")
import regex
import translate_prototype as T
from cost7 import TEN_B, sixty_four

def cold():
    for v in vars(T).values():
        if callable(getattr(v, "cache_clear", None)): v.cache_clear()
    re.purge(); regex.purge()

for name, rules in (("10 ordinary rules", TEN_B), ("64 ordinary rules", sixty_four())):
    body = json.dumps({"rules": [{"id": f"r{i}", "condition": {"regex": p}, "action": "block"} for i, p in enumerate(rules)],
                       "version": 7}, sort_keys=True).encode()
    signed_hash = hashlib.sha256(body).hexdigest()
    c, w, h = [], [], []
    for _ in range(5):
        cold(); t0 = time.thread_time(); T.check_plan(rules); c.append(time.thread_time() - t0)
        t0 = time.thread_time(); T.check_plan(rules); w.append(time.thread_time() - t0)
        t0 = time.thread_time()
        for _ in range(1000):
            skip = hashlib.sha256(body).hexdigest() == signed_hash
        h.append((time.thread_time() - t0) / 1000)
    print(f"{name} ({len(body)} B body): check_plan cold {statistics.median(c)*1e3:.2f} ms, warm {statistics.median(w)*1e3:.2f} ms "
          f"-> x100k orgs {statistics.median(c)*1e5:.0f} s; hash skip {statistics.median(h)*1e6:.1f} us -> x100k orgs "
          f"{statistics.median(h)*1e5:.2f} s")
