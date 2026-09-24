# ub-078: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 23400 (78.0/s) qualified 22982 (76.61/s) FP-blocks 418 (0.01786) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'qualified': 22982, 'policy_block_fp': 418}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=22982 p50=46.208 p90=52.7358 p99=70.6024 p99.9=75.9252 max=106.7252 mean=36.8806
T_fw_addon_nohold: n=22982 p50=9.5821 p90=12.3265 p99=15.3211 p99.9=19.8281 max=31.3745 mean=9.0271
T_fw_addon_sse: n=16089 p50=49.314 p90=53.7153 p99=71.1566 p99.9=84.9767 max=106.7252 mean=48.7768
T_fw_addon_json: n=6893 p50=9.6577 p90=12.5671 p99=15.5632 p99.9=20.7848 max=31.3745 mean=9.1136
T_addon_first_sse: n=16089 p50=9.4953 p90=12.7214 p99=27.3061 p99.9=33.469 max=50.5043 mean=9.2331
T_addon_total_sse: n=16089 p50=9.55 p90=12.2357 p99=15.2488 p99.9=19.7085 max=26.4717 mean=8.99
T_addon_total_json: n=6893 p50=9.6577 p90=12.5671 p99=15.5632 p99.9=20.7848 max=31.3745 mean=9.1136
T_release_lag_max: n=16089 p50=49.314 p90=53.7153 p99=71.1566 p99.9=84.9767 max=106.7252 mean=48.7768
client_ttft_sse: n=16089 p50=159.541 p90=162.7631 p99=177.3205 p99.9=183.5133 max=200.536 mean=159.2808
lateness: n=23400 p50=0.0845 p90=0.0942 p99=0.1056 p99.9=0.1294 max=0.3391 mean=0.0844
loadgen: [{'vm': 'rv-pbu-lg-3', 'busy_max': 8.4, 'busy_mean': 5.0, 'late_max_us': 454, 'conn_opens': 223, 'max_inflight': 211}, {'vm': 'rv-pbu-lg-4', 'busy_max': 5.1, 'busy_mean': 4.8, 'late_max_us': 339, 'conn_opens': 223, 'max_inflight': 211}]
wire per client request: {'client_to_gw': 8171.6, 'gw_to_client': 56408.8, 'gw_to_provider': 8877.6, 'provider_to_gw': 57074.2}
gateway cores 2.741 cpu-ms/req 35.256 (workers 31.195, owners 4.055, redis 0.191)
worker util {'n': 18, 'min': 0.075, 'median': 0.132, 'max': 0.186, 'all_sorted': [0.075, 0.105, 0.109, 0.111, 0.114, 0.118, 0.12, 0.129, 0.132, 0.132, 0.142, 0.145, 0.155, 0.159, 0.162, 0.165, 0.167, 0.186]}; per-core schedstat max 0.148 mean 0.117; procstat max 0.259
gpu: {'0': {'samples': 298, 'sm_mean': 11.4, 'sm_max': 26.0, 'mem_mean': 2.1, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 12.4, 'sm_max': 29.0, 'mem_mean': 2.3, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 199.5, 'launcher': 59.3, 'owner': 3065.4, 'worker': 3627.2}; fds {'redis': 0, 'launcher': 7, 'owner': 131, 'worker': 1280}
W t_input_ns: {'n': 23367, 'mean_ms': 7.6956, 'p50_ms': 8.3558, 'p90_ms': 10.4202, 'p99_ms': 13.1727, 'p99.9_ms': 17.1704, 'max_cum_ms': 28.4715}
W t_admit_ns: {'n': 23367, 'mean_ms': 0.0903, 'p50_ms': 0.0865, 'p90_ms': 0.106, 'p99_ms': 0.1362, 'p99.9_ms': 0.553, 'max_cum_ms': 2.8912}
W t_tokenize_ns: {'n': 23367, 'mean_ms': 2.9203, 'p50_ms': 2.9327, 'p90_ms': 4.4892, 'p99_ms': 5.2756, 'p99.9_ms': 6.3242, 'max_cum_ms': 7.6311}
W t_det_scan_ns: {'n': 23367, 'mean_ms': 0.1057, 'p50_ms': 0.1029, 'p90_ms': 0.1382, 'p99_ms': 0.1833, 'p99.9_ms': 0.2161, 'max_cum_ms': 1.4745}
W t_guard_wait_ns: {'n': 23367, 'mean_ms': 4.217, 'p50_ms': 4.8169, 'p90_ms': 5.2101, 'p99_ms': 7.3728, 'p99.9_ms': 11.7309, 'max_cum_ms': 22.9596}
W guard_owner_rtt_ns: {'n': 23367, 'mean_ms': 4.3991, 'p50_ms': 5.0135, 'p90_ms': 5.4723, 'p99_ms': 7.5694, 'p99.9_ms': 11.3377, 'max_cum_ms': 22.3906}
W guard_queue_ns: {'n': 23367, 'mean_ms': 0.1103, 'p50_ms': 0.107, 'p90_ms': 0.1321, 'p99_ms': 0.1628, 'p99.9_ms': 0.2447, 'max_cum_ms': 4.1818}
W guard_exec_ns: {'n': 23367, 'mean_ms': 3.5838, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 9.4896}
W dispatch_headers_ns: {'n': 22952, 'mean_ms': 1514.4476, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7952.4004, 'p99.9_ms': 8153.727, 'max_cum_ms': 8132.2192}
W release_lag_ns: {'n': 3574456, 'mean_ms': 19.8839, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.0619}
W holdback_wait_ns: {'n': 3483616, 'mean_ms': 20.3368, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.0169}
W release_processing_ns: {'n': 3574456, 'mean_ms': 0.0639, 'p50_ms': 0.0632, 'p90_ms': 0.0814, 'p99_ms': 0.107, 'p99.9_ms': 0.1362, 'max_cum_ms': 1.3241}
W t_finalize_ns: {'n': 22965, 'mean_ms': 0.2536, 'p50_ms': 0.2488, 'p90_ms': 0.3052, 'p99_ms': 0.3707, 'p99.9_ms': 0.4157, 'max_cum_ms': 1.0753}
W loop_lag_ns: {'n': 53640, 'mean_ms': 0.7171, 'p50_ms': 0.008, 'p90_ms': 2.474, 'p99_ms': 7.2417, 'p99.9_ms': 10.6824, 'max_cum_ms': 19.6193}
W guard_windows_per_request: {'n': 23367, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 23385, 'mean_ms': 3.5834, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 9.4896}
O guard_batch_windows: {'n': 23385, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 23385, 'mean_ms': 0.1103, 'p50_ms': 0.107, 'p90_ms': 0.1321, 'p99_ms': 0.1628, 'p99.9_ms': 0.2447, 'max_cum_ms': 4.1818}
worker counts: {'admitted': 23367, 'audit_enqueued': 46332, 'audit_written': 46333, 'background_round_trips': 10728, 'disposition_ALLOW': 22949, 'disposition_BLOCK': 418, 'guard_windows': 38487, 'lease_granted_tokens{org="org-a"}': 22187520, 'lease_refills': 108, 'provider_calls': 22949, 'provider_connections_opened': 3582, 'quota_admitted_tokens{org="org-a"}': 22122048, 'requests_by_round_trips{n="0"}': 23259, 'requests_by_round_trips{n="1"}': 108, 'shared_state_round_trips': 108}
owner counts: {'guard_batches': 23385, 'guard_windows': 38513, 'owner_requests': 23385, 'owner_windows': 38513}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [26802.292245528068, 26961.884746460342], 'input_queue_cap': [70.0, 71.0], 'guard_queue_cap_tokens': [26802.0, 26961.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 241220, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 241220.63020975262}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 242656, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 242656.96271814307}]
notes: []
