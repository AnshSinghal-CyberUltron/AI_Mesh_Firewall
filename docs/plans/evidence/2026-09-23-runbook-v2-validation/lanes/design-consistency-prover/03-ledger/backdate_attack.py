"""Feed a back-dated, post-run ledger commit to the REAL GW02 gate code (byte-identical copy)."""
import json, os, subprocess, sys, time
from dataclasses import replace
from pathlib import Path

from gateway_v2.contracts.parity.c2 import generate_c2
from gateway_v2.contracts.parity.clock import FrozenClock
from gateway_v2.contracts.parity.differ import classify, diff_signature
from gateway_v2.contracts.parity.ledger import LedgerTimestampError, load_ledger
from gateway_v2.contracts.parity.replay import replay

REPO = Path(sys.argv[1]); REPO.mkdir(parents=True)
def git(*args, env=None):
    return subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True, text=True,
                          env={**os.environ, **(env or {})}).stdout.strip()
git("init", "-q", "-b", "main"); git("config", "user.email", "agent@example.invalid"); git("config", "user.name", "agent")
ledger = REPO / "expected_diff_ledger.json"
ledger.write_text(json.dumps({"schema": "amf.expected-diff.v1", "entries": []}, indent=2) + "\n")
git("add", "."); git("commit", "-q", "-m", "empty ledger")

# ---- the diff run: records its start time and the commit it executed at ----
rec = generate_c2(1, FrozenClock(epoch=1_704_067_200))[0]
left = replay(rec, FrozenClock(epoch=1_704_067_200), lambda _t: "allow")          # v1
right = replace(left, disposition="redact", transformations=("mask",))            # v2 behaviour change
run_started_at = int(time.time()); run_commit = git("rev-parse", "HEAD")
first = classify(left, right, load_ledger(ledger), run_started_at, None)
print(f"RUN @ {run_started_at} commit {run_commit[:10]}: bucket={first.bucket} sig={first.reason}")

time.sleep(2)  # the ledger entry is written strictly AFTER the run observed the diff
backdated = run_started_at - 3600
sig = diff_signature(left, right)
ledger.write_text(json.dumps({"schema": "amf.expected-diff.v1", "entries": [{
    "rule_id": "P8-BACKTICK", "row_id": "10.2.2-benign-inline-code", "c3_score": "fpr=1.00",
    "diff_signature": sig, "commit_timestamp": backdated}]}, indent=2) + "\n")
stamp = f"@{backdated} +0000"
git("add", "."); git("commit", "-q", "-m", "post-hoc ledger entry",
    env={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})
entry_commit = git("rev-parse", "HEAD")
ct = int(git("log", "-1", "--format=%ct", "--", ledger.name)); at = int(git("log", "-1", "--format=%at", "--", ledger.name))
print(f"ENTRY commit {entry_commit[:10]} created at wall time {int(time.time())} (> run {run_started_at})")
print(f"  git %ct (committer) = {ct}   git %at (author) = {at}   JSON field = {backdated}")
print(f"  is entry commit an ancestor of the run commit? ",
      subprocess.run(["git", "merge-base", "--is-ancestor", entry_commit, run_commit], cwd=REPO).returncode == 0)
entries = load_ledger(ledger)
for label, gct in (("CI wiring as shipped (git_commit_time=None, classify() default)", None),
                   ("git_commit_time from `git log -1 --format=%ct`", ct),
                   ("git_commit_time from `git log -1 --format=%at`", at)):
    try:
        res = classify(left, right, entries, run_started_at, gct)
        print(f"  [{label}] -> bucket={res.bucket} rule={res.rule_id}   ACCEPTED (post-hoc entry excused)")
    except LedgerTimestampError as e:
        print(f"  [{label}] -> REJECTED: {e}")
