# m86-025: strict FAIL | load-knee FAIL (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 7500 (25.0/s) qualified 7423 (24.74/s) FP-blocks 77 (0.01027) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'qualified': 7423, 'policy_block_fp': 77}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=7423 p50=14.692 p90=21.1839 p99=56.4524 p99.9=75.5779 max=81.6638 mean=15.9078
T_fw_addon_nohold: n=7423 p50=14.4259 p90=16.8767 p99=21.5567 p99.9=22.736 max=26.9034 mean=12.9624
T_fw_addon_sse: n=5187 p50=14.7737 p90=34.847 p99=60.885 p99.9=76.3128 max=81.6638 mean=17.1769
T_fw_addon_json: n=2236 p50=14.5176 p90=16.8305 p99=21.5555 p99.9=23.0449 max=26.5744 mean=12.964
T_addon_first_sse: n=5187 p50=14.3599 p90=19.8686 p99=33.9887 p99.9=40.9363 max=54.9631 mean=13.2153
T_addon_total_sse: n=5187 p50=14.4023 p90=16.9172 p99=21.5771 p99.9=22.3967 max=26.9034 mean=12.9618
T_addon_total_json: n=2236 p50=14.5176 p90=16.8305 p99=21.5555 p99.9=23.0449 max=26.5744 mean=12.964
T_release_lag_max: n=503 p50=54.2433 p90=60.8968 p99=76.3128 p99.9=81.6638 max=81.6638 mean=52.6698
client_ttft_sse: n=5187 p50=164.4107 p90=169.9134 p99=184.0398 p99.9=190.9725 max=205.0376 mean=163.2629
lateness: n=7500 p50=0.0874 p90=0.1004 p99=0.1196 p99.9=0.1551 max=0.2306 mean=0.0888
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 17.6, 'busy_mean': 4.2, 'late_max_us': 325, 'conn_opens': 72, 'max_inflight': 70}, {'vm': 'rv-pbu-lg-2', 'busy_max': 16.6, 'busy_mean': 3.9, 'late_max_us': 206, 'conn_opens': 72, 'max_inflight': 70}]
wire per client request: {'client_to_gw': 9165.0, 'gw_to_client': 57390.8, 'gw_to_provider': 9867.3, 'provider_to_gw': 57676.7}
gateway cores 1.075 cpu-ms/req 43.138 (workers 34.998, owners 8.122, redis 0.286)
worker util {'n': 18, 'min': 0.029, 'median': 0.046, 'max': 0.086}; per-core schedstat max 0.092 mean 0.047; procstat max 0.095
gpu: {'0': {'samples': 298, 'sm_mean': 9.1, 'sm_max': 30.0, 'mem_mean': 7.8, 'fb_mb_max': 876.0}, '1': {'samples': 298, 'sm_mean': 6.7, 'sm_max': 30.0, 'mem_mean': 5.9, 'fb_mb_max': 876.0}}
rss max total by role (MB): {'redis': 92.3, 'launcher': 59.8, 'owner': 6157.2, 'worker': 5362.8}; fds {'redis': 0, 'launcher': 7, 'owner': 129, 'worker': 729}
W t_input_ns: {'n': 7494, 'mean_ms': 11.7408, 'p50_ms': 13.3038, 'p90_ms': 15.401, 'p99_ms': 20.3162, 'p99.9_ms': 21.1026, 'max_cum_ms': 23.0991}
W t_admit_ns: {'n': 7494, 'mean_ms': 0.0909, 'p50_ms': 0.0886, 'p90_ms': 0.0947, 'p99_ms': 0.1142, 'p99.9_ms': 0.5284, 'max_cum_ms': 2.7857}
W t_tokenize_ns: {'n': 7494, 'mean_ms': 3.0604, 'p50_ms': 3.0638, 'p90_ms': 4.8169, 'p99_ms': 5.4723, 'p99.9_ms': 6.5864, 'max_cum_ms': 7.9824}
W t_det_scan_ns: {'n': 7494, 'mean_ms': 0.1033, 'p50_ms': 0.0988, 'p90_ms': 0.1403, 'p99_ms': 0.1894, 'p99.9_ms': 0.2284, 'max_cum_ms': 1.4474}
W t_guard_wait_ns: {'n': 7494, 'mean_ms': 8.1559, 'p50_ms': 9.6338, 'p90_ms': 10.027, 'p99_ms': 14.3524, 'p99.9_ms': 15.2699, 'max_cum_ms': 16.9323}
W guard_owner_rtt_ns: {'n': 7494, 'mean_ms': 8.3393, 'p50_ms': 9.7649, 'p90_ms': 10.1581, 'p99_ms': 14.6145, 'p99.9_ms': 14.8767, 'max_cum_ms': 16.8397}
W guard_queue_ns: {'n': 7494, 'mean_ms': 0.1193, 'p50_ms': 0.1162, 'p90_ms': 0.1423, 'p99_ms': 0.1772, 'p99.9_ms': 0.3748, 'max_cum_ms': 0.9081}
W guard_exec_ns: {'n': 7494, 'mean_ms': 7.5348, 'p50_ms': 8.9784, 'p90_ms': 9.2406, 'p99_ms': 13.697, 'p99.9_ms': 13.8281, 'max_cum_ms': 13.9641}
W dispatch_headers_ns: {'n': 7427, 'mean_ms': 1544.9824, 'p50_ms': 149.9464, 'p90_ms': 5939.1345, 'p99_ms': 7885.2915, 'p99.9_ms': 8153.727, 'max_cum_ms': 8136.4004}
W release_lag_ns: {'n': 1159764, 'mean_ms': 19.8853, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 80.151}
W holdback_wait_ns: {'n': 1130489, 'mean_ms': 20.3342, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 80.0577}
W release_processing_ns: {'n': 1159764, 'mean_ms': 0.0644, 'p50_ms': 0.0637, 'p90_ms': 0.0835, 'p99_ms': 0.1091, 'p99.9_ms': 0.1403, 'max_cum_ms': 0.7103}
W t_finalize_ns: {'n': 7430, 'mean_ms': 0.2547, 'p50_ms': 0.2509, 'p90_ms': 0.3092, 'p99_ms': 0.3707, 'p99.9_ms': 0.4239, 'max_cum_ms': 0.4482}
W loop_lag_ns: {'n': 53660, 'mean_ms': 0.6261, 'p50_ms': 0.0201, 'p90_ms': 1.1715, 'p99_ms': 5.931, 'p99.9_ms': 7.8316, 'max_cum_ms': 15.376}
W guard_windows_per_request: {'n': 7494, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 7498, 'mean_ms': 7.5356, 'p50_ms': 8.9784, 'p90_ms': 9.2406, 'p99_ms': 13.697, 'p99.9_ms': 13.8281, 'max_cum_ms': 13.9641}
O guard_batch_windows: {'n': 7498, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 7498, 'mean_ms': 0.1193, 'p50_ms': 0.1162, 'p90_ms': 0.1423, 'p99_ms': 0.1772, 'p99.9_ms': 0.3748, 'max_cum_ms': 0.9081}
worker counts: {'admitted': 7494, 'audit_enqueued': 14924, 'audit_written': 14924, 'background_round_trips': 10732, 'disposition_ALLOW': 7417, 'disposition_BLOCK': 77, 'guard_windows': 12360, 'lease_granted_tokens{org="org-a"}': 6984960, 'lease_refills': 34, 'provider_calls': 7417, 'provider_connections_opened': 995, 'quota_admitted_tokens{org="org-a"}': 7047163, 'requests_by_round_trips{n="0"}': 7460, 'requests_by_round_trips{n="1"}': 34, 'shared_state_round_trips': 34}
owner counts: {'guard_batches': 7498, 'guard_windows': 12368, 'owner_requests': 7498, 'owner_windows': 12368}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [10216.727194476982, 10413.095534492311], 'input_queue_cap': [1.0], 'guard_queue_cap_tokens': [204.0, 208.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 1839, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 91950.54475029284}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 1874, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 93717.8598104308}]
notes: []
