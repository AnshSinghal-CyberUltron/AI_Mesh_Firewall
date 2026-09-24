# t22-078: strict FAIL | load-knee FAIL (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 23400 (78.0/s) qualified 22805 (76.02/s) FP-blocks 412 (0.01761) expected-blocks 0 infra 183 (0.00782051282051282) drops 0 safety 0 detection-misses 0
by class: {'benign': {'qualified': 22805, 'policy_block_fp': 412, 'infra_error': 183}}
infra reasons: {'http_503': 182, 'incomplete': 182, 'unjoined': 182, 'disposition_missing': 182, 'stage_canon_missing': 182, 'stage_det_missing': 182, 'stage_sem_missing': 182, 'stage_resolve_missing': 182, 'stage_dispatch_missing': 182, 'stage_out_missing': 182, 'stage_audit_missing': 182, 'block_on_unavailable_sem': 1}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 182, '403 http_403 type=policy_violation code=blocked_by_policy': 1}

T_fw_addon: n=22805 p50=13.0863 p90=18.7831 p99=56.907 p99.9=73.8271 max=91.956 mean=16.1093
T_fw_addon_nohold: n=22805 p50=12.8868 p90=16.6023 p99=19.9612 p99.9=25.0293 max=30.0968 mean=13.119
T_fw_addon_sse: n=15971 p50=13.1581 p90=34.3154 p99=57.996 p99.9=74.3602 max=91.956 mean=17.3625
T_fw_addon_json: n=6834 p50=12.9479 p90=16.5573 p99=19.8109 p99.9=24.011 max=27.3347 mean=13.1807
T_addon_first_sse: n=15971 p50=12.7534 p90=16.7707 p99=31.6749 p99.9=37.3152 max=51.1128 mean=13.244
T_addon_total_sse: n=15971 p50=12.8624 p90=16.6091 p99=20.0547 p99.9=25.5045 max=30.0968 mean=13.0926
T_addon_total_json: n=6834 p50=12.9479 p90=16.5573 p99=19.8109 p99.9=24.011 max=27.3347 mean=13.1807
T_release_lag_max: n=1582 p50=52.68 p90=58.0445 p99=74.3602 p99.9=80.5063 max=91.956 mean=53.0766
client_ttft_sse: n=15971 p50=162.8022 p90=166.8141 p99=181.6946 p99.9=187.4025 max=201.1315 mean=163.2918
lateness: n=23400 p50=0.0842 p90=0.0938 p99=0.1048 p99.9=0.1263 max=0.3392 mean=0.0841
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 17.2, 'busy_mean': 5.0, 'late_max_us': 455, 'conn_opens': 219, 'max_inflight': 210}, {'vm': 'rv-pbu-lg-2', 'busy_max': 15.0, 'busy_mean': 4.8, 'late_max_us': 231, 'conn_opens': 225, 'max_inflight': 211}]
wire per client request: {'client_to_gw': 8151.2, 'gw_to_client': 55985.5, 'gw_to_provider': 8790.8, 'provider_to_gw': 56650.8}
gateway cores 2.647 cpu-ms/req 34.045 (workers 34.039, owners 0.0, redis 0.192)
worker util {'n': 18, 'min': 0.106, 'median': 0.145, 'max': 0.193}; per-core schedstat max 0.143 mean 0.131; procstat max 0.319
gpu: {'0': {'samples': 298, 'sm_mean': 15.9, 'sm_max': 26.0, 'mem_mean': 9.1, 'fb_mb_max': 2206.0}, '1': {'samples': 298, 'sm_mean': 16.0, 'sm_max': 24.0, 'mem_mean': 9.1, 'fb_mb_max': 2206.0}}
rss max total by role (MB): {'redis': 351.7, 'triton': 1012.7, 'launcher': 26.2, 'worker': 3920.9}; fds {'redis': 0, 'triton': 0, 'launcher': 3, 'worker': 1395}
W t_input_ns: {'n': 23210, 'mean_ms': 11.7384, 'p50_ms': 11.5999, 'p90_ms': 14.6145, 'p99_ms': 17.6947, 'p99.9_ms': 21.889, 'max_cum_ms': 27.5993}
W t_admit_ns: {'n': 23391, 'mean_ms': 0.0949, 'p50_ms': 0.0906, 'p90_ms': 0.1121, 'p99_ms': 0.1444, 'p99.9_ms': 0.6185, 'max_cum_ms': 2.7753}
W t_tokenize_ns: {'n': 23391, 'mean_ms': 3.0189, 'p50_ms': 3.031, 'p90_ms': 4.6858, 'p99_ms': 5.4723, 'p99.9_ms': 6.3242, 'max_cum_ms': 7.8094}
W t_det_scan_ns: {'n': 23209, 'mean_ms': 0.1066, 'p50_ms': 0.1039, 'p90_ms': 0.1403, 'p99_ms': 0.1853, 'p99.9_ms': 0.2243, 'max_cum_ms': 2.1254}
W t_guard_wait_ns: {'n': 23210, 'mean_ms': 8.2407, 'p50_ms': 8.0937, 'p90_ms': 9.7649, 'p99_ms': 12.6484, 'p99.9_ms': 16.9083, 'max_cum_ms': 21.611}
W dispatch_headers_ns: {'n': 22794, 'mean_ms': 1513.2207, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7952.4004, 'p99.9_ms': 8153.727, 'max_cum_ms': 8135.7071}
W release_lag_ns: {'n': 3548435, 'mean_ms': 19.8911, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.1479}
W holdback_wait_ns: {'n': 3458237, 'mean_ms': 20.3408, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.0657}
W release_processing_ns: {'n': 3548435, 'mean_ms': 0.0674, 'p50_ms': 0.0671, 'p90_ms': 0.0865, 'p99_ms': 0.1121, 'p99.9_ms': 0.1423, 'max_cum_ms': 1.9494}
W t_finalize_ns: {'n': 22785, 'mean_ms': 0.2624, 'p50_ms': 0.257, 'p90_ms': 0.3174, 'p99_ms': 0.3871, 'p99.9_ms': 0.4321, 'max_cum_ms': 0.8424}
W loop_lag_ns: {'n': 53640, 'mean_ms': 0.7253, 'p50_ms': 0.0139, 'p90_ms': 2.3757, 'p99_ms': 7.2417, 'p99.9_ms': 9.8959, 'max_cum_ms': 14.9624}
W guard_windows_per_request: {'n': 23391, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
worker counts: {'admitted': 23391, 'audit_enqueued': 45995, 'audit_written': 45995, 'background_round_trips': 10728, 'disposition_ALLOW': 22796, 'disposition_BLOCK': 414, 'guard_rpc_errors': 1, 'guard_unavailable_findings': 1, 'lease_granted_tokens{org="org-a"}': 21982080, 'lease_refills': 107, 'provider_calls': 22796, 'provider_connections_opened': 3151, 'quota_admitted_tokens{org="org-a"}': 22149625, 'requests_by_round_trips{n="0"}': 23284, 'requests_by_round_trips{n="1"}': 107, 'shared_state_round_trips': 107, 'shed{reason="guard_queue"}': 182}
owner counts: {}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [21756.87349732643, 21757.40563756465, 21758.67455252781, 22065.79461432838, 22745.666213816774, 22746.477594264124, 23857.713980046217, 24374.243606209155, 24499.83868671323, 25244.08321706887, 25647.818393789425, 26012.760340266355, 28099.377478703507, 28467.725063403173, 30553.201907293103, 32332.361807941918, 57971.41004325191, 63307.11989407819], 'input_queue_cap': [2.0, 4.0], 'guard_queue_cap_tokens': [435.0, 441.0, 454.0, 477.0, 487.0, 489.0, 504.0, 512.0, 520.0, 561.0, 569.0, 611.0, 646.0, 1159.0, 1266.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 1839, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 91950.54475029284}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 1874, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 93717.8598104308}]
notes: []
