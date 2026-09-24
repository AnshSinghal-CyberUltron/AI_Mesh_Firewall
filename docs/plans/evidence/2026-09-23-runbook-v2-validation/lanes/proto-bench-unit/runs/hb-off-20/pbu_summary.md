# hb-off-20: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 12000 (40.0/s) qualified 11826 (39.42/s) FP-blocks 174 (0.0145) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'qualified': 11826, 'policy_block_fp': 174}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=11826 p50=12.8496 p90=17.0618 p99=20.3285 p99.9=23.4333 max=29.1674 mean=13.1215
T_fw_addon_nohold: n=11826 p50=9.4913 p90=12.1006 p99=14.8306 p99.9=19.2501 max=24.9672 mean=8.9519
T_fw_addon_sse: n=11826 p50=12.8496 p90=17.0618 p99=20.3285 p99.9=23.4333 max=29.1674 mean=13.1215
T_fw_addon_json: n=0
T_addon_first_sse: n=11826 p50=9.3958 p90=11.9833 p99=14.5557 p99.9=18.5954 max=24.9828 mean=8.8516
T_addon_total_sse: n=11826 p50=9.4913 p90=12.1006 p99=14.8306 p99.9=19.2501 max=24.9672 mean=8.9519
T_addon_total_json: n=0
T_release_lag_max: n=11826 p50=12.8496 p90=17.0618 p99=20.3285 p99.9=23.4333 max=29.1674 mean=13.1215
client_ttft_sse: n=11826 p50=159.4416 p90=162.0339 p99=164.6251 p99.9=168.653 max=174.9967 mean=158.8996
lateness: n=12000 p50=0.0859 p90=0.0973 p99=0.1098 p99.9=0.1364 max=0.237 mean=0.0862
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 8.0, 'busy_mean': 4.9, 'late_max_us': 274, 'conn_opens': 117, 'max_inflight': 110}, {'vm': 'rv-pbu-lg-2', 'busy_max': 5.0, 'busy_mean': 4.6, 'late_max_us': 228, 'conn_opens': 117, 'max_inflight': 110}]
wire per client request: {'client_to_gw': 10759.9, 'gw_to_client': 81174.3, 'gw_to_provider': 12779.0, 'provider_to_gw': 81117.3}
gateway cores 1.617 cpu-ms/req 40.563 (workers 36.407, owners 4.144, redis 0.237)
worker util {'n': 18, 'min': 0.047, 'median': 0.089, 'max': 0.119}; per-core schedstat max 0.087 mean 0.071; procstat max 0.173
gpu: {'0': {'samples': 298, 'sm_mean': 5.9, 'sm_max': 15.0, 'mem_mean': 1.1, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 6.2, 'sm_max': 15.0, 'mem_mean': 1.1, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 204.5, 'launcher': 59.7, 'owner': 3065.4, 'worker': 3622.6}; fds {'redis': 0, 'launcher': 7, 'owner': 126, 'worker': 886}
W t_input_ns: {'n': 11981, 'mean_ms': 7.6547, 'p50_ms': 8.3558, 'p90_ms': 10.2892, 'p99_ms': 12.7795, 'p99.9_ms': 15.9252, 'max_cum_ms': 23.6637}
W t_admit_ns: {'n': 11981, 'mean_ms': 0.0949, 'p50_ms': 0.0906, 'p90_ms': 0.1101, 'p99_ms': 0.1464, 'p99.9_ms': 0.5939, 'max_cum_ms': 3.1107}
W t_tokenize_ns: {'n': 11981, 'mean_ms': 2.8222, 'p50_ms': 2.8344, 'p90_ms': 4.2926, 'p99_ms': 4.948, 'p99.9_ms': 6.1276, 'max_cum_ms': 7.3783}
W t_det_scan_ns: {'n': 11981, 'mean_ms': 0.114, 'p50_ms': 0.1101, 'p90_ms': 0.1464, 'p99_ms': 0.1976, 'p99.9_ms': 0.2263, 'max_cum_ms': 0.823}
W t_guard_wait_ns: {'n': 11981, 'mean_ms': 4.262, 'p50_ms': 4.8824, 'p90_ms': 5.2756, 'p99_ms': 7.3728, 'p99.9_ms': 11.0756, 'max_cum_ms': 18.3214}
W guard_owner_rtt_ns: {'n': 11981, 'mean_ms': 4.4457, 'p50_ms': 5.079, 'p90_ms': 5.4723, 'p99_ms': 7.6349, 'p99.9_ms': 10.9445, 'max_cum_ms': 18.0896}
W guard_queue_ns: {'n': 11981, 'mean_ms': 0.1146, 'p50_ms': 0.1121, 'p90_ms': 0.1362, 'p99_ms': 0.1669, 'p99.9_ms': 0.7905, 'max_cum_ms': 1.0546}
W guard_exec_ns: {'n': 11981, 'mean_ms': 3.6203, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 8.8594}
W dispatch_headers_ns: {'n': 11813, 'mean_ms': 150.8378, 'p50_ms': 149.9464, 'p90_ms': 152.0435, 'p99_ms': 152.0435, 'p99.9_ms': 156.2378, 'max_cum_ms': 160.9283}
W release_lag_ns: {'n': 2664247, 'mean_ms': 0.0364, 'p50_ms': 0.0346, 'p90_ms': 0.0484, 'p99_ms': 0.0691, 'p99.9_ms': 0.0896, 'max_cum_ms': 0.7513}
W release_processing_ns: {'n': 2664247, 'mean_ms': 0.0364, 'p50_ms': 0.0346, 'p90_ms': 0.0484, 'p99_ms': 0.0691, 'p99.9_ms': 0.0896, 'max_cum_ms': 0.7513}
W t_finalize_ns: {'n': 11825, 'mean_ms': 0.2222, 'p50_ms': 0.2161, 'p90_ms': 0.2611, 'p99_ms': 0.3133, 'p99.9_ms': 0.3502, 'max_cum_ms': 0.4857}
W loop_lag_ns: {'n': 53640, 'mean_ms': 0.6437, 'p50_ms': 0.0048, 'p90_ms': 1.8924, 'p99_ms': 6.3242, 'p99.9_ms': 8.7163, 'max_cum_ms': 13.5865}
W guard_windows_per_request: {'n': 11981, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 11991, 'mean_ms': 3.6193, 'p50_ms': 4.2926, 'p90_ms': 4.5548, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 8.8594}
O guard_batch_windows: {'n': 11991, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 11991, 'mean_ms': 0.1146, 'p50_ms': 0.1121, 'p90_ms': 0.1362, 'p99_ms': 0.1669, 'p99.9_ms': 0.7905, 'max_cum_ms': 1.0546}
worker counts: {'admitted': 11981, 'audit_enqueued': 23806, 'audit_written': 23806, 'background_round_trips': 10728, 'disposition_ALLOW': 11807, 'disposition_BLOCK': 174, 'guard_windows': 19717, 'lease_granted_tokens{org="org-a"}': 10888320, 'lease_refills': 53, 'provider_calls': 11807, 'provider_connections_opened': 2692, 'quota_admitted_tokens{org="org-a"}': 11375140, 'requests_by_round_trips{n="0"}': 11928, 'requests_by_round_trips{n="1"}': 53, 'shared_state_round_trips': 53}
owner counts: {'guard_batches': 11991, 'guard_windows': 19728, 'owner_requests': 11991, 'owner_windows': 19728}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [25729.182604852933, 26396.122167030782], 'input_queue_cap': [2.0], 'guard_queue_cap_tokens': [514.0, 527.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4631, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 231562.64344367638}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 4751, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 237565.09950327704}]
notes: []
