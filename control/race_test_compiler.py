"""
Bundle C1 — concurrent race test for PolicyCompiler.push_to_redis.

Runs N threads each invoking push_to_redis() against the same org. After all
threads finish, verifies:
  1. Final version_key in Redis == starting + N (no lost updates).
  2. Bundle stored at compiled_key has bundle["version"] == version_key value
     (no torn (version, payload) pair).
  3. No exceptions raised.

Run inside the control container:
  docker compose exec -T control python /tmp/race_test_compiler.py
"""

import os
import json
import django
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai_mesh_control.settings")
django.setup()

import redis  # noqa: E402
from django.conf import settings  # noqa: E402
from policy.compiler import PolicyCompiler, REDIS_KEY_COMPILED, REDIS_KEY_VERSION  # noqa: E402

ORG_SLUG = "default"
NUM_THREADS = 20

r = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

version_key = f"{REDIS_KEY_VERSION}:{ORG_SLUG}"
compiled_key = f"{REDIS_KEY_COMPILED}:{ORG_SLUG}"

start_version = int(r.get(version_key) or 0)
print(f"[race] starting version = {start_version}")
print(f"[race] launching {NUM_THREADS} concurrent push_to_redis() calls")

errors: list[str] = []
versions_seen: list[int] = []
versions_lock = threading.Lock()


def worker(i: int) -> None:
    try:
        compiler = PolicyCompiler()
        ok = compiler.push_to_redis(trigger=f"race_test_{i}")
        if not ok:
            with versions_lock:
                errors.append(f"thread {i}: push returned False")
    except Exception as exc:
        with versions_lock:
            errors.append(f"thread {i}: {type(exc).__name__}: {exc}")


with ThreadPoolExecutor(max_workers=NUM_THREADS) as pool:
    futures = [pool.submit(worker, i) for i in range(NUM_THREADS)]
    for f in as_completed(futures):
        f.result()

final_version = int(r.get(version_key) or 0)
bundle_raw = r.get(compiled_key)
bundle = json.loads(bundle_raw) if bundle_raw else {}
bundle_version = bundle.get("version")

print(f"[race] final version_key value = {final_version}")
print(f"[race] bundle.version inside payload = {bundle_version}")
print(f"[race] expected final version = {start_version + NUM_THREADS}")
print(f"[race] errors: {len(errors)}")
for e in errors:
    print(f"  - {e}")

exit_code = 0

# Check 1: no lost updates
if final_version != start_version + NUM_THREADS:
    print(
        f"FAIL: lost updates: expected {start_version + NUM_THREADS}, "
        f"got {final_version} (delta = {final_version - start_version})"
    )
    exit_code = 1
else:
    print("PASS: all version bumps accounted for (no lost updates)")

# Check 2: stored bundle version matches version key (no torn pair)
if bundle_version != final_version:
    print(
        f"FAIL: torn pair: version_key={final_version} but bundle.version="
        f"{bundle_version}"
    )
    exit_code = 1
else:
    print(f"PASS: (version, bundle) pair consistent at v={final_version}")

if errors:
    print(f"FAIL: {len(errors)} thread errors")
    exit_code = 1
else:
    print("PASS: no exceptions in any thread")

sys.exit(exit_code)
