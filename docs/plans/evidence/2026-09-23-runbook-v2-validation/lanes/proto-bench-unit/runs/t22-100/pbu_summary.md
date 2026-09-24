# t22-100: strict FAIL | load-knee FAIL (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 30000 (100.0/s) qualified 28657 (95.52/s) FP-blocks 505 (0.01683) expected-blocks 0 infra 838 (0.027933333333333334) drops 0 safety 0 detection-misses 0
by class: {'benign': {'infra_error': 838, 'policy_block_fp': 505, 'qualified': 28657}}
infra reasons: {'http_503': 834, 'incomplete': 834, 'unjoined': 834, 'disposition_missing': 834, 'stage_canon_missing': 834, 'stage_det_missing': 834, 'stage_sem_missing': 834, 'stage_resolve_missing': 834, 'stage_dispatch_missing': 834, 'stage_out_missing': 834, 'stage_audit_missing': 834, 'block_on_unavailable_sem': 4}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 834, '403 http_403 type=policy_violation code=blocked_by_policy': 4}

T_fw_addon: n=28657 p50=14.0411 p90=19.47 p99=57.2216 p99.9=75.5763 max=91.2201 mean=16.9224
T_fw_addon_nohold: n=28657 p50=13.7188 p90=17.1913 p99=20.4616 p99.9=26.5267 max=33.5258 mean=13.8082
T_fw_addon_sse: n=20091 p50=14.1784 p90=36.7207 p99=58.2609 p99.9=76.9229 max=91.2201 mean=18.2152
T_fw_addon_json: n=8566 p50=13.7982 p90=17.3361 p99=20.4366 p99.9=26.4047 max=30.1502 mean=13.8902
T_addon_first_sse: n=20091 p50=13.6134 p90=17.3334 p99=33.0043 p99.9=37.9519 max=74.1341 mean=13.9781
T_addon_total_sse: n=20091 p50=13.6884 p90=17.1381 p99=20.4841 p99.9=26.805 max=33.5258 mean=13.7733
T_addon_total_json: n=8566 p50=13.7982 p90=17.3361 p99=20.4366 p99.9=26.4047 max=30.1502 mean=13.8902
T_release_lag_max: n=2032 p50=53.666 p90=58.2374 p99=76.9229 p99.9=80.1686 max=91.2201 mean=53.7632
client_ttft_sse: n=20091 p50=163.6513 p90=167.386 p99=183.0925 p99.9=187.9635 max=224.1915 mean=164.0236
lateness: n=30000 p50=0.0848 p90=0.0945 p99=0.1092 p99.9=0.1502 max=0.3103 mean=0.0854
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 8.3, 'busy_mean': 5.2, 'late_max_us': 567, 'conn_opens': 270, 'max_inflight': 253}, {'vm': 'rv-pbu-lg-2', 'busy_max': 5.4, 'busy_mean': 5.0, 'late_max_us': 310, 'conn_opens': 273, 'max_inflight': 255}]
wire per client request: {'client_to_gw': 7987.7, 'gw_to_client': 54964.2, 'gw_to_provider': 8629.7, 'provider_to_gw': 55615.4}
gateway cores 3.322 cpu-ms/req 33.33 (workers 33.326, owners 0.0, redis 0.181)
worker util {'n': 18, 'min': 0.142, 'median': 0.187, 'max': 0.244}; per-core schedstat max 0.174 mean 0.165; procstat max 0.34
gpu: {'0': {'samples': 298, 'sm_mean': 19.9, 'sm_max': 31.0, 'mem_mean': 11.3, 'fb_mb_max': 2206.0}, '1': {'samples': 298, 'sm_mean': 19.7, 'sm_max': 32.0, 'mem_mean': 11.2, 'fb_mb_max': 2206.0}}
rss max total by role (MB): {'redis': 549.8, 'triton': 1012.7, 'launcher': 26.2, 'worker': 3929.1}; fds {'redis': 0, 'triton': 0, 'launcher': 3, 'worker': 1566}
W t_input_ns: {'n': 29157, 'mean_ms': 12.2997, 'p50_ms': 12.1242, 'p90_ms': 15.6631, 'p99_ms': 18.219, 'p99.9_ms': 23.724, 'max_cum_ms': 29.4723}
W t_admit_ns: {'n': 29991, 'mean_ms': 0.0956, 'p50_ms': 0.0906, 'p90_ms': 0.1132, 'p99_ms': 0.1464, 'p99.9_ms': 0.6349, 'max_cum_ms': 2.7753}
W t_tokenize_ns: {'n': 29991, 'mean_ms': 3.2722, 'p50_ms': 3.326, 'p90_ms': 5.1446, 'p99_ms': 5.931, 'p99.9_ms': 6.9796, 'max_cum_ms': 7.9559}
W t_det_scan_ns: {'n': 29157, 'mean_ms': 0.1134, 'p50_ms': 0.1111, 'p90_ms': 0.1485, 'p99_ms': 0.1894, 'p99.9_ms': 0.2202, 'max_cum_ms': 2.1254}
W t_guard_wait_ns: {'n': 29157, 'mean_ms': 8.5133, 'p50_ms': 8.2903, 'p90_ms': 10.5513, 'p99_ms': 13.0417, 'p99.9_ms': 18.4812, 'max_cum_ms': 24.0627}
W dispatch_headers_ns: {'n': 28667, 'mean_ms': 1512.5507, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7952.4004, 'p99.9_ms': 8153.727, 'max_cum_ms': 8135.7071}
W release_lag_ns: {'n': 4477737, 'mean_ms': 19.8918, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.6323, 'max_cum_ms': 100.1479}
W holdback_wait_ns: {'n': 4363928, 'mean_ms': 20.3411, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.6323, 'max_cum_ms': 100.0657}
W release_processing_ns: {'n': 4477737, 'mean_ms': 0.0678, 'p50_ms': 0.0671, 'p90_ms': 0.0886, 'p99_ms': 0.1162, 'p99.9_ms': 0.1485, 'max_cum_ms': 1.9494}
W t_finalize_ns: {'n': 28680, 'mean_ms': 0.2702, 'p50_ms': 0.2642, 'p90_ms': 0.3338, 'p99_ms': 0.3994, 'p99.9_ms': 0.4485, 'max_cum_ms': 1.6513}
W loop_lag_ns: {'n': 53640, 'mean_ms': 0.7602, 'p50_ms': 0.0157, 'p90_ms': 2.8672, 'p99_ms': 7.6349, 'p99.9_ms': 10.2892, 'max_cum_ms': 14.9624}
W guard_windows_per_request: {'n': 29991, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
worker counts: {'admitted': 29991, 'audit_enqueued': 57837, 'audit_written': 57838, 'background_round_trips': 10728, 'disposition_ALLOW': 28648, 'disposition_BLOCK': 509, 'guard_rpc_errors': 4, 'guard_unavailable_findings': 4, 'lease_granted_tokens{org="org-a"}': 28556160, 'lease_refills': 139, 'provider_calls': 28648, 'provider_connections_opened': 4239, 'quota_admitted_tokens{org="org-a"}': 28407498, 'requests_by_round_trips{n="0"}': 29852, 'requests_by_round_trips{n="1"}': 139, 'shared_state_round_trips': 139, 'shed{reason="guard_queue"}': 834}
owner counts: {}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [21756.87349732643, 21757.40563756465, 21758.67455252781, 22065.79461432838, 22745.666213816774, 22746.477594264124, 23857.713980046217, 24374.243606209155, 24499.83868671323, 25244.08321706887, 25647.818393789425, 26012.760340266355, 28099.377478703507, 28467.725063403173, 30553.201907293103, 32332.361807941918, 57971.41004325191, 63307.11989407819], 'input_queue_cap': [2.0, 4.0], 'guard_queue_cap_tokens': [435.0, 441.0, 454.0, 477.0, 487.0, 489.0, 504.0, 512.0, 520.0, 561.0, 569.0, 611.0, 646.0, 1159.0, 1266.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 1839, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 91950.54475029284}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 1874, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 93717.8598104308}]
notes: []
