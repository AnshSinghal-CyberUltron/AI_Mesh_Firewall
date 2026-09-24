import os, sys
def _names(): return sorted(k for k in os.environ if k.startswith(("AWS", "BOTO")))
print("[probe] import-time AWS* names:", _names(), file=sys.stderr)
def pytest_configure(config): print("[probe] configure AWS* names:", _names(), file=sys.stderr)
def pytest_runtest_setup(item): print("[probe] setup", item.name, _names(), file=sys.stderr)
def pytest_sessionfinish(session, exitstatus): print("[probe] finish AWS* names:", _names(), file=sys.stderr)
