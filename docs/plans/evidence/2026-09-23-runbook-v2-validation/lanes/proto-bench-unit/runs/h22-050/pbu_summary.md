# h22-050: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 15000 (50.0/s) qualified 14765 (49.22/s) FP-blocks 229 (0.01527) expected-blocks 0 infra 6 (0.0004) drops 0 safety 0 detection-misses 0
by class: {'benign': {'policy_block_fp': 229, 'qualified': 14765, 'infra_error': 6}}
infra reasons: {'http_503': 3, 'incomplete': 3, 'unjoined': 3, 'disposition_missing': 3, 'stage_canon_missing': 3, 'stage_det_missing': 3, 'stage_sem_missing': 3, 'stage_resolve_missing': 3, 'stage_dispatch_missing': 3, 'stage_out_missing': 3, 'stage_audit_missing': 3, 'block_on_unavailable_sem': 3}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 3, '403 http_403 type=policy_violation code=blocked_by_policy': 3}

T_fw_addon: n=14765 p50=9.799 p90=14.0636 p99=52.9584 p99.9=72.9609 max=223.0174 mean=11.951
T_fw_addon_nohold: n=14765 p50=9.5613 p90=12.0311 p99=14.9459 p99.9=19.5367 max=162.912 mean=8.9595
T_fw_addon_sse: n=10336 p50=9.8574 p90=29.8263 p99=54.5423 p99.9=73.6088 max=223.0174 mean=13.2136
T_fw_addon_json: n=4429 p50=9.6192 p90=12.2755 p99=14.933 p99.9=18.217 max=22.0217 mean=9.0045
T_addon_first_sse: n=10336 p50=9.4612 p90=12.725 p99=27.1773 p99.9=33.6658 max=115.45 mean=9.1664
T_addon_total_sse: n=10336 p50=9.5396 p90=11.9228 p99=14.9459 p99.9=19.5367 max=162.912 mean=8.9403
T_addon_total_json: n=4429 p50=9.6192 p90=12.2755 p99=14.933 p99.9=18.217 max=22.0217 mean=9.0045
T_release_lag_max: n=994 p50=49.2007 p90=54.6087 p99=73.302 p99.9=223.0174 max=223.0174 mean=49.3882
client_ttft_sse: n=10336 p50=159.5084 p90=162.7826 p99=177.2144 p99.9=183.6803 max=265.4557 mean=159.2101
lateness: n=15000 p50=0.0899 p90=0.1036 p99=0.1186 p99.9=0.1335 max=0.2023 mean=0.0907
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 7.6, 'busy_mean': 4.5, 'late_max_us': 521, 'conn_opens': 149, 'max_inflight': 137}, {'vm': 'rv-pbu-lg-2', 'busy_max': 7.6, 'busy_mean': 4.4, 'late_max_us': 208, 'conn_opens': 149, 'max_inflight': 137}]
wire per client request: {'client_to_gw': 8354.5, 'gw_to_client': 56389.5, 'gw_to_provider': 9192.8, 'provider_to_gw': 57053.9}
gateway cores 1.788 cpu-ms/req 35.887 (workers 31.828, owners 4.05, redis 0.215)
worker util {'n': 18, 'min': 0.064, 'median': 0.091, 'max': 0.106}; per-core schedstat max 0.102 mean 0.078; procstat max 0.129
gpu: {'0': {'samples': 299, 'sm_mean': 7.5, 'sm_max': 21.0, 'mem_mean': 1.4, 'fb_mb_max': 434.0}, '1': {'samples': 299, 'sm_mean': 7.6, 'sm_max': 22.0, 'mem_mean': 1.4, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 174.7, 'launcher': 59.3, 'owner': 3065.7, 'worker': 3624.1}; fds {'redis': 0, 'launcher': 7, 'owner': 126, 'worker': 997}
W t_input_ns: {'n': 14984, 'mean_ms': 7.6092, 'p50_ms': 8.2903, 'p90_ms': 10.1581, 'p99_ms': 12.5174, 'p99.9_ms': 16.0563, 'max_cum_ms': 381.0444}
W t_admit_ns: {'n': 14987, 'mean_ms': 0.0986, 'p50_ms': 0.0937, 'p90_ms': 0.1203, 'p99_ms': 0.1587, 'p99.9_ms': 0.6922, 'max_cum_ms': 2.84}
W t_tokenize_ns: {'n': 14987, 'mean_ms': 2.8239, 'p50_ms': 2.8344, 'p90_ms': 4.2926, 'p99_ms': 4.8169, 'p99.9_ms': 6.1276, 'max_cum_ms': 7.3744}
W t_det_scan_ns: {'n': 14984, 'mean_ms': 0.099, 'p50_ms': 0.0998, 'p90_ms': 0.1213, 'p99_ms': 0.1485, 'p99.9_ms': 0.1833, 'max_cum_ms': 0.8319}
W t_guard_wait_ns: {'n': 14984, 'mean_ms': 4.2192, 'p50_ms': 4.7514, 'p90_ms': 5.1446, 'p99_ms': 7.1762, 'p99.9_ms': 11.4688, 'max_cum_ms': 376.1411}
W guard_owner_rtt_ns: {'n': 14984, 'mean_ms': 4.3868, 'p50_ms': 4.948, 'p90_ms': 5.3412, 'p99_ms': 7.3728, 'p99.9_ms': 10.6824, 'max_cum_ms': 376.3447}
W guard_queue_ns: {'n': 14981, 'mean_ms': 0.1075, 'p50_ms': 0.106, 'p90_ms': 0.1254, 'p99_ms': 0.1505, 'p99.9_ms': 0.1935, 'max_cum_ms': 0.9402}
W guard_exec_ns: {'n': 14981, 'mean_ms': 3.5685, 'p50_ms': 4.2271, 'p90_ms': 4.4237, 'p99_ms': 6.4553, 'p99.9_ms': 6.5864, 'max_cum_ms': 9.0492}
W dispatch_headers_ns: {'n': 14757, 'mean_ms': 1518.1716, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7885.2915, 'p99.9_ms': 8153.727, 'max_cum_ms': 8131.8348}
W release_lag_ns: {'n': 2289935, 'mean_ms': 19.8837, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 231.6626}
W holdback_wait_ns: {'n': 2231336, 'mean_ms': 20.3396, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 231.6168}
W release_processing_ns: {'n': 2289935, 'mean_ms': 0.0645, 'p50_ms': 0.0632, 'p90_ms': 0.0855, 'p99_ms': 0.1142, 'p99.9_ms': 0.1485, 'max_cum_ms': 1.2723}
W t_finalize_ns: {'n': 14761, 'mean_ms': 0.2673, 'p50_ms': 0.2611, 'p90_ms': 0.3338, 'p99_ms': 0.3953, 'p99.9_ms': 0.4403, 'max_cum_ms': 0.5202}
W loop_lag_ns: {'n': 53610, 'mean_ms': 0.711, 'p50_ms': 0.0193, 'p90_ms': 1.3189, 'p99_ms': 7.1107, 'p99.9_ms': 9.2406, 'max_cum_ms': 202.7189}
W guard_windows_per_request: {'n': 14987, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 14988, 'mean_ms': 3.5693, 'p50_ms': 4.2271, 'p90_ms': 4.4237, 'p99_ms': 6.4553, 'p99.9_ms': 6.5864, 'max_cum_ms': 9.0492}
O guard_batch_windows: {'n': 14988, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 14988, 'mean_ms': 0.1075, 'p50_ms': 0.106, 'p90_ms': 0.1254, 'p99_ms': 0.1505, 'p99.9_ms': 0.1935, 'max_cum_ms': 0.9402}
worker counts: {'admitted': 14987, 'audit_enqueued': 29745, 'audit_written': 29745, 'background_round_trips': 10722, 'disposition_ALLOW': 14753, 'disposition_BLOCK': 231, 'guard_deadline_expired': 3, 'guard_unavailable_findings': 3, 'guard_windows': 24723, 'lease_granted_tokens{org="org-a"}': 13969920, 'lease_refills': 68, 'provider_calls': 14753, 'provider_connections_opened': 2562, 'quota_admitted_tokens{org="org-a"}': 14222503, 'requests_by_round_trips{n="0"}': 14919, 'requests_by_round_trips{n="1"}': 68, 'shared_state_round_trips': 68, 'shed{reason="guard_queue"}': 3}
owner counts: {'guard_batches': 14988, 'guard_deadline_expired': 3, 'guard_windows': 24740, 'owner_requests': 14991, 'owner_windows': 24748}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [26778.36769188868, 26979.31316245365], 'input_queue_cap': [2.0], 'guard_queue_cap_tokens': [535.0, 539.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4820, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 241005.30922699813}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4856, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 242813.81846208285}]
notes: []
