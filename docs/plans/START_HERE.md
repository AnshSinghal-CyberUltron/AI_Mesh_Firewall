# Start here: the first task and first verification

**Goal:** a safe, Haiku-free implementation that is qualified at p99 firewall overhead below 20 ms and at least 1,064 fully evaluated successful requests/second on one signed profile. These are proposed acceptance criteria, not achieved results.

## First task: T00 - identify what is actually running

Do not change a model, Docker image, cloud resource or policy yet. Establish the checkout commit, running image IDs/digests, served frontend build, actual gateway/control URLs, model/runtime versions, tenant policy version, authorized test environment, and last safe rollback artifact.

From the unpacked directory, run the read-only evidence collector against the verified checkout:

```bash
export REPO_ROOT=/absolute/path/to/verified/AI_Mesh_Firewall
export PACK_ROOT=/absolute/path/to/aimesh_implementation_pack
export EVIDENCE_DIR="$HOME/aimesh-evidence/T00-$(date -u +%Y%m%dT%H%M%SZ)"
bash "$PACK_ROOT/tools/collect_baseline.sh" "$REPO_ROOT" "$EVIDENCE_DIR"
```

It writes restricted evidence, but does not build/restart containers or deploy anything. Exit 2 means partial collection. Resolve the nonzero statuses; do not call T00 complete merely because the command ran.

**Verify T00:** match one real staging-browser request ID to the intended backend. Confirm that the serving image and frontend build correspond to the candidate source. Fill `templates/environment-manifest.example.json` with observations. A second engineer must be able to locate the exact environment from that manifest without old chat IPs or hidden terminal state.

**Success:** authorized staging environment, reproducible build identity, correct browser-to-backend mapping, known policy/model versions, and a safe rollback artifact.

**Fail/stop:** unknown image revision, wrong tenant/host, manual container patch, missing rollback, or a browser pointing to another deployment.

## Next: T01 sign-off, then two foundational tracks

**T01:** approve the exact percentile/latency boundary, workload and output-release mode. Resolve action precedence including MONITOR/FLAG, exact output semantic backend, windows/overlap, numerical quality gates and error behavior. The template is intentionally unsigned and incomplete.

**T03 / G1 is the first application-code task:** repair and prove timing. With the real staging gateway and a controlled token-emitting upstream, change first-token delay from 50 ms to 2,000 ms. Provider TTFT changes; measured firewall overhead must not inherit the extra 1,950 ms. Inject known 5 ms input and 7 ms output delays; those must appear. Insert middle-stream withholding and verify it is not hidden as provider time. Confirm the real frontend shows the correct backend metric and skipped/degraded stages.

**T02 / G0 proceeds alongside it:** at least 300 attacks across eight families plus 300 benign inputs, and separately labeled redaction/compatibility fixtures. Freeze held-out data and quality thresholds before tuning. Publish per-posture, per-language and per-family quality results.

Do not remove Haiku before replacement-quality, fail-closed, output and migration gates pass. Do not interpret a 2.14 ms model call as the complete nine-stage gateway result.

## Read next

`IMPLEMENTATION_RUNBOOK.html` provides navigable task cards T00-T28. The Markdown file is the repository-friendly master. `diagrams/README.md` explains how to paste the three editable diagram sources into Eraser; no live workspace was published in this session.
