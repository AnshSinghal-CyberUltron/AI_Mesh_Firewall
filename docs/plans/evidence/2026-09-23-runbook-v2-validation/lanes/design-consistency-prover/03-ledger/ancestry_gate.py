"""(1) The runbook's timestamp rule, even with HONEST timestamps, is laundered by re-running.
(2) Proposed check: an entry is pre-registered iff the commit that introduced it is an ancestor
    of the commit at which the FIRST run that observed its signature executed. Commit ancestry
    is hash-committed, so no date manipulation can change it."""
import json, os, subprocess, sys, time
from dataclasses import replace
from pathlib import Path

from gateway_v2.contracts.parity.c2 import generate_c2
from gateway_v2.contracts.parity.clock import FrozenClock
from gateway_v2.contracts.parity.differ import classify, diff_signature
from gateway_v2.contracts.parity.ledger import load_ledger
from gateway_v2.contracts.parity.replay import replay

REPO = Path(sys.argv[1]); REPO.mkdir(parents=True)
LEDGER = "expected_diff_ledger.json"
def git(*a, env=None, check=True):
    p = subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True, env={**os.environ, **(env or {})})
    if check and p.returncode: raise RuntimeError(p.stderr)
    return p
def head(): return git("rev-parse", "HEAD").stdout.strip()
def write_ledger(entries):
    (REPO / LEDGER).write_text(json.dumps({"schema": "amf.expected-diff.v1", "entries": entries}, indent=2) + "\n")
def commit(msg, date=None):
    git("add", "-A"); env = {"GIT_AUTHOR_DATE": f"@{date} +0000", "GIT_COMMITTER_DATE": f"@{date} +0000"} if date else None
    git("commit", "-q", "--allow-empty", "-m", msg, env=env); return head()

rec = generate_c2(1, FrozenClock(epoch=1_704_067_200))[0]
left = replay(rec, FrozenClock(epoch=1_704_067_200), lambda _t: "allow")
right = replace(left, disposition="redact", transformations=("mask",))
SIG = diff_signature(left, right)
ENTRY = {"rule_id": "P8", "row_id": "10.2.2-benign-inline-code", "c3_score": "x", "diff_signature": SIG}

OBS = []  # append-only observation log, written by CI (run order, run commit, signatures seen)
def ci_run(name):
    started = int(time.time()); sha = head()
    res = classify(left, right, load_ledger(REPO / LEDGER), started, None)
    OBS.append({"run": name, "run_commit": sha, "started_at": started, "signatures": [SIG]})
    return started, sha, res.bucket

def introducing_commit(sig, tip):
    for c in git("rev-list", "--topo-order", "--reverse", tip, "--", LEDGER).stdout.split():
        blob = git("show", f"{c}:{LEDGER}", check=False).stdout
        if blob and any(e.get("diff_signature") == sig for e in json.loads(blob)["entries"]):
            return c
    return None

def proposed_gate(sig, tip):
    first = next((o for o in OBS if sig in o["signatures"]), None)
    intro = introducing_commit(sig, tip)
    if intro is None: return "REJECT (no entry)"
    if first is None: return "ACCEPT (signature never observed before this entry)"
    ok = git("merge-base", "--is-ancestor", intro, first["run_commit"], check=False).returncode == 0
    return ("ACCEPT" if ok else "REJECT") + f" (entry {intro[:8]} ancestor-of first-observing run {first['run']}@{first['run_commit'][:8]}: {ok})"

git("init", "-q", "-b", "main"); git("config", "user.email", "a@example.invalid"); git("config", "user.name", "a")
write_ledger([]); commit("empty ledger")

print("S1 honest PRE-registration: entry committed, THEN the first run observes the diff")
write_ledger([{**ENTRY, "commit_timestamp": int(time.time())}]); commit("pre-registered entry")
time.sleep(1); s, sha, b = ci_run("S1-run1"); print(f"   run1 bucket={b}; proposed gate: {proposed_gate(SIG, head())}")

print("S2/S3 post-hoc: run1 observes UNEXPECTED, entry added afterwards, then RE-RUN (GW21 L3139 flow)")
OBS.clear(); git("checkout", "-q", "-b", "posthoc", "HEAD~1")         # branch without the entry
write_ledger([]); commit("feature work")
s1, r1, b1 = ci_run("run1"); print(f"   run1 @ {s1} commit {r1[:8]} bucket={b1}")
time.sleep(2)
write_ledger([{**ENTRY, "commit_timestamp": int(time.time())}]); honest = commit("ledger entry (HONEST dates)")
time.sleep(1)
s2, r2, b2 = ci_run("run2")
print(f"   run2 @ {s2} commit {r2[:8]}: runbook timestamp rule (entry {int(time.time())-3}<run2) -> bucket={b2}  <= laundered, no back-dating needed")
print(f"   proposed gate: {proposed_gate(SIG, head())}")
git("reset", "-q", "--hard", "HEAD~1"); write_ledger([{**ENTRY, "commit_timestamp": s1 - 3600}])
back = commit("ledger entry (BACK-DATED)", date=s1 - 3600)
print(f"   back-dated variant: proposed gate: {proposed_gate(SIG, head())}")

print("S4 rewrite history so the entry precedes run1's changes (new SHAs); observation log keeps run1's SHA")
git("checkout", "-q", "-b", "rewritten", f"{r1}~1")
write_ledger([{**ENTRY, "commit_timestamp": s1 - 7200}]); commit("entry inserted before", date=s1 - 7200)
git("cherry-pick", "--allow-empty", "--empty=keep", r1, env={"GIT_COMMITTER_DATE": f"@{s1} +0000"})
print(f"   rewritten tip {head()[:8]} (run1 was {r1[:8]}); proposed gate: {proposed_gate(SIG, head())}")
print("OBSERVATION LOG:", json.dumps([{k: (v[:8] if k == 'run_commit' else v) for k, v in o.items() if k != 'signatures'} for o in OBS]))
