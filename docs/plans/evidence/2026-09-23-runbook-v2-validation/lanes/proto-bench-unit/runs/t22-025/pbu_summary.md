# t22-025: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 7500 (25.0/s) qualified 7403 (24.68/s) FP-blocks 97 (0.01293) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'qualified': 7403, 'policy_block_fp': 97}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=7403 p50=13.0164 p90=17.8493 p99=55.6873 p99.9=72.7933 max=92.2851 mean=15.7996
T_fw_addon_nohold: n=7403 p50=12.7655 p90=15.6754 p99=18.3962 p99.9=22.3503 max=25.0872 mean=12.8025
T_fw_addon_sse: n=5175 p50=13.1064 p90=33.874 p99=56.6734 p99.9=72.8622 max=92.2851 mean=17.0617
T_fw_addon_json: n=2228 p50=12.8335 p90=16.0244 p99=18.4638 p99.9=22.3345 max=25.0872 mean=12.8679
T_addon_first_sse: n=5175 p50=12.7268 p90=16.5342 p99=32.7473 p99.9=37.2273 max=57.5012 mean=13.1027
T_addon_total_sse: n=5175 p50=12.7422 p90=15.5565 p99=18.3851 p99.9=22.6765 max=24.4235 mean=12.7743
T_addon_total_json: n=2228 p50=12.8335 p90=16.0244 p99=18.4638 p99.9=22.3345 max=25.0872 mean=12.8679
T_release_lag_max: n=499 p50=52.6041 p90=56.6774 p99=72.9967 p99.9=92.2851 max=92.2851 mean=52.4394
client_ttft_sse: n=5175 p50=162.7642 p90=166.615 p99=182.7668 p99.9=187.2503 max=207.53 mean=163.1503
lateness: n=7500 p50=0.0899 p90=0.1051 p99=0.1272 p99.9=0.2033 max=0.3047 mean=0.0915
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 17.7, 'busy_mean': 4.3, 'late_max_us': 282, 'conn_opens': 74, 'max_inflight': 70}, {'vm': 'rv-pbu-lg-2', 'busy_max': 17.2, 'busy_mean': 4.0, 'late_max_us': 304, 'conn_opens': 74, 'max_inflight': 70}]
wire per client request: {'client_to_gw': 9229.7, 'gw_to_client': 56909.3, 'gw_to_provider': 10024.7, 'provider_to_gw': 57564.0}
gateway cores 0.977 cpu-ms/req 39.206 (workers 39.191, owners 0.0, redis 0.292)
worker util {'n': 18, 'min': 0.029, 'median': 0.056, 'max': 0.087}; per-core schedstat max 0.065 mean 0.05; procstat max 0.121
gpu: {'0': {'samples': 298, 'sm_mean': 4.5, 'sm_max': 12.0, 'mem_mean': 2.5, 'fb_mb_max': 2206.0}, '1': {'samples': 298, 'sm_mean': 4.9, 'sm_max': 15.0, 'mem_mean': 2.6, 'fb_mb_max': 2206.0}}
rss max total by role (MB): {'redis': 92.5, 'triton': 1012.4, 'launcher': 26.2, 'worker': 3902.4}; fds {'redis': 0, 'triton': 0, 'launcher': 3, 'worker': 837}
W t_input_ns: {'n': 7497, 'mean_ms': 11.4681, 'p50_ms': 11.4688, 'p90_ms': 13.566, 'p99_ms': 16.7117, 'p99.9_ms': 20.5783, 'max_cum_ms': 23.2702}
W t_admit_ns: {'n': 7497, 'mean_ms': 0.0983, 'p50_ms': 0.0937, 'p90_ms': 0.1142, 'p99_ms': 0.1526, 'p99.9_ms': 0.6758, 'max_cum_ms': 2.7753}
W t_tokenize_ns: {'n': 7497, 'mean_ms': 3.0589, 'p50_ms': 3.1621, 'p90_ms': 4.6203, 'p99_ms': 5.2756, 'p99.9_ms': 6.4553, 'max_cum_ms': 7.6231}
W t_det_scan_ns: {'n': 7497, 'mean_ms': 0.1075, 'p50_ms': 0.108, 'p90_ms': 0.1341, 'p99_ms': 0.169, 'p99.9_ms': 0.2017, 'max_cum_ms': 0.6783}
W t_guard_wait_ns: {'n': 7497, 'mean_ms': 7.9359, 'p50_ms': 7.8316, 'p90_ms': 8.7163, 'p99_ms': 11.2067, 'p99.9_ms': 15.401, 'max_cum_ms': 18.7549}
W dispatch_headers_ns: {'n': 7411, 'mean_ms': 1541.5694, 'p50_ms': 149.9464, 'p90_ms': 5939.1345, 'p99_ms': 7885.2915, 'p99.9_ms': 8153.727, 'max_cum_ms': 8131.4604}
W release_lag_ns: {'n': 1157339, 'mean_ms': 19.89, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 80.1564}
W holdback_wait_ns: {'n': 1127893, 'mean_ms': 20.3397, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 80.1023}
W release_processing_ns: {'n': 1157339, 'mean_ms': 0.0678, 'p50_ms': 0.0671, 'p90_ms': 0.0876, 'p99_ms': 0.1132, 'p99.9_ms': 0.1464, 'max_cum_ms': 0.8807}
W t_finalize_ns: {'n': 7414, 'mean_ms': 0.2626, 'p50_ms': 0.2591, 'p90_ms': 0.3174, 'p99_ms': 0.383, 'p99.9_ms': 0.4321, 'max_cum_ms': 0.5181}
W loop_lag_ns: {'n': 53730, 'mean_ms': 0.6125, 'p50_ms': 0.0188, 'p90_ms': 1.0895, 'p99_ms': 5.931, 'p99.9_ms': 8.0282, 'max_cum_ms': 11.4381}
W guard_windows_per_request: {'n': 7497, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
worker counts: {'admitted': 7497, 'audit_enqueued': 14911, 'audit_written': 14911, 'background_round_trips': 10746, 'disposition_ALLOW': 7400, 'disposition_BLOCK': 97, 'lease_granted_tokens{org="org-a"}': 6779520, 'lease_refills': 33, 'provider_calls': 7400, 'provider_connections_opened': 1032, 'quota_admitted_tokens{org="org-a"}': 7108104, 'requests_by_round_trips{n="0"}': 7464, 'requests_by_round_trips{n="1"}': 33, 'shared_state_round_trips': 33}
owner counts: {}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [21756.87349732643, 21757.40563756465, 21758.67455252781, 22065.79461432838, 22745.666213816774, 22746.477594264124, 23857.713980046217, 24374.243606209155, 24499.83868671323, 25244.08321706887, 25647.818393789425, 26012.760340266355, 28099.377478703507, 28467.725063403173, 30553.201907293103, 32332.361807941918, 57971.41004325191, 63307.11989407819], 'input_queue_cap': [2.0, 4.0], 'guard_queue_cap_tokens': [435.0, 441.0, 454.0, 477.0, 487.0, 489.0, 504.0, 512.0, 520.0, 561.0, 569.0, 611.0, 646.0, 1159.0, 1266.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 1839, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 91950.54475029284}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 1874, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 93717.8598104308}]
notes: []
