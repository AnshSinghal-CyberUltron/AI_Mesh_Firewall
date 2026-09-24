# Harness analysis (sut, policy={'blocks_total': 518, 'expected_blocks': 0, 'false_positive_blocks': 518, 'other_blocks': 0, 'benign_offered': 29570, 'false_positive_rate': 0.017517754480892798, 'policy_misses': 0, 'latency_client_total_ms': {'policy_block_fp': {'n': 518, 'min': 3.3421, 'p50': 7.7233, 'p90': 11.4096, 'p99': 18.3145, 'p999': 27.8044, 'max': 27.8044, 'mean': 8.443}}, 'block_rule': {'statuses': [400, 403, 422, 451], 'envelope_re': '(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat'}}) — FAIL

| check | result |
|---|---|
| p99_T_fw_addon_lt_slo | FAIL |
| infra_error_rate_le_budget | PASS |
| zero_schedule_drops | PASS |
| zero_safety_failures | PASS |
| run_valid | PASS |
| qualified_nonempty | PASS |

- offered: 29570 over 300 s = 98.57 req/s (configured 98.0)
- qualified: 29052 (96.84 req/s; dispositions ['ALLOW', 'FLAG', 'REDACT'], source auto)
- infra errors (the error-budget gate): 0 (rate 0.0); reasons: {}
- policy stratum (not qualified, not infra errors): expected blocks 0, FALSE-POSITIVE blocks 518 of 29570 benign (FP rate 0.017517754480892798), other blocks 0, policy misses 0
- policy cohort latency policy_block_fp (client total ms): n=518 p50=7.7233 p90=11.4096 p99=18.3145 p99.9=27.8044 max=27.8044 mean=8.443
- safety failures: 0 {}
- schedule drops (>5.0 ms): 0; lateness ms: n=29570 p50=0.0846 p90=0.0946 p99=0.1063 p99.9=0.1227 max=0.1611 mean=0.0833
- join: {'joined': 29052, 'by_nonce_fallback': 0, 'provider_records': 36347, 'provider_rids_with_multiple_calls': 0, 'provider_orphans': None}
- valid: True []

| metric (ms) | distribution |
|---|---|
| T_addon_total | n=29052 p50=7.8539 p90=11.5467 p99=17.0981 p99.9=23.2785 max=30.8946 mean=7.9596 |
| T_addon_first | n=29052 p50=7.8495 p90=11.7976 p99=24.8411 p99.9=32.0445 max=51.4915 mean=8.1678 |
| T_release_lag_max | n=29052 p50=45.1828 p90=51.6051 p99=69.6965 p99.9=76.7396 max=101.1798 mean=35.7531 |
| T_release_lag_max_arrival | n=29052 p50=27.9638 p90=33.1068 p99=39.147 p99.9=45.6347 max=54.7686 mean=23.4334 |
| T_fw_addon | n=29052 p50=45.1828 p90=51.6051 p99=69.6965 p99.9=76.7396 max=101.1798 mean=35.7531 |
| T_fw_addon_sse | n=20341 p50=47.7091 p90=53.2861 p99=70.5996 p99.9=79.3062 max=101.1798 mean=47.6309 |
| T_fw_addon_json | n=8711 p50=7.9228 p90=11.5471 p99=16.7167 p99.9=22.5946 max=28.8928 mean=8.0174 |
| client_ttft_sse | n=20341 p50=157.8635 p90=161.9783 p99=177.4223 p99.9=182.8089 max=201.5846 mean=158.2781 |
| provider_sched_err_last | n=29052 p50=0.0424 p90=0.0867 p99=0.11 p99.9=0.1299 max=0.2074 mean=0.0452 |
| provider_sched_err_max_per_stream | n=29052 p50=0.0887 p90=0.129 p99=0.1679 p99.9=0.2038 max=0.4279 mean=0.0849 |
| provider_write_max | n=29052 p50=0.0183 p90=0.0244 p99=0.0347 p99.9=0.0455 max=0.107 mean=0.0177 |

- release lag: 29052 sampled streams joined, 0 unmappable (content length differs)
- loadgen CPU (measurement phase): [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1p-098-r3/rv-split-lg-3/lg', 'samples': 300, 'busy_max': 19.79823074140198, 'busy_mean': 7.87, 'machine': 'c4-highcpu-8', 'zone': 'asia-south1-b'}]
- provider CPU: {'samples': 430, 'busy_max': 19.580152110366754, 'busy_p95': 9.133828351072504}
- completeness: [{'dir': '/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs/s1p-098-r3/rv-split-lg-3/lg', 'scheduled': 36971, 'recorded': 36971, 'interrupted': False}]
- health olg rv-split-lg-3: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 11, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 11, 'TcpExtTCPTimeouts': 0, 'TcpOutSegs': 3509698, 'TcpInSegs': 5728886}
- health synthprov rv-split-prov-3: gc_cycles=0 sched_latency_max_ms=0.098304 tcp={'TcpRetransSegs': 29, 'TcpExtTCPFastRetrans': 0, 'TcpExtTCPLossProbes': 9, 'TcpExtTCPTimeouts': 22, 'TcpOutSegs': 5870705, 'TcpInSegs': 5108099}
