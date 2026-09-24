# v1 stage-execution + provider-record verification (20 requests)

Run: runs/verify20 (olg 2 req/s x 10 s, headline-22M corpus, seed 101, 70/30 SSE/JSON) through v1
(HEAD 52a584e9) on rv-v1-sut-1 -> synthprov on rv-v1-prov-1 (ttft 150 ms, itl 20 ms).
Harness binaries actually used (sha256 on the VMs): see runs/verify20/binaries-used.txt (an EARLIER build than the final harness; verification is re-run with the final build before measuring)

- olg: scheduled=20 recorded=20 ok=20 drops=0 (rv-v1-lg-1/lg/olg.log)
- synthprov: 20 records, all joined 1:1 to the client records (analysis/summary.md "join"), content
  sha256 of every client-assembled response == provider content (qualified 20/20, errors 0)
- v1 audit events (Postgres policy_enforcementevent; 'request' for JSON, 'stream_complete' for SSE):
  20/20 matched to olg records via client_correlation_id (= olg x-request-id); dispositions ALLOW=20
- v1 stage actions over the 20 requests (lg-v1aug/v1_adapter_report.json):
  {'auth:allow': 20, 'input_scan:allow': 20, 'kill_switch:allow': 20, 'model_input:allow': 20, 'model_output:allow': 20, 'model_routing:allow': 20, 'output_guardrail:allow': 20, 'policy:allow': 20, 'rate_limit:allow': 20}
  => all nine v1 stages EXECUTED (action != skip) on every request.
- Streaming responses carried x-zeroshield-stream-scan-mode: output_guard (smoke: setup/smoke_stream.py).
- T_fw_addon (gross): SSE p50 731 ms (T_addon_first p50 665 ms: output-guard holdback), JSON p50 68 ms.

## Re-run on the validated harness build (runs/verify20b, 2026-09-23 ~10:12 UTC)
Binaries: harness build 5af9594a2631 (bin/ as validated in evidence/harness-builder/runs/val-6000-r7);
sha256 on the VMs in runs/verify20b/binaries-used.txt. Same result: olg 20/20 ok, 0 drops; synthprov 20 records,
all rid_src=nonce (v1 does not forward x-request-id), all joined 1:1, content sha equal (qualified 20/20);
v1 audit events matched 20/20, dispositions ALLOW=20, all nine stages action=allow (none skipped).
T_fw_addon SSE p50 765 ms, JSON p50 81 ms.
