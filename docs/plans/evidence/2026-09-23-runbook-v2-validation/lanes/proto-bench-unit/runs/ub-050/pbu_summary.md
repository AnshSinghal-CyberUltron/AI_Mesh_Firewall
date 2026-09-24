# ub-050: strict FAIL | load-knee PASS (sut)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 15000 (50.0/s) qualified 14755 (49.18/s) FP-blocks 245 (0.01633) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'policy_block_fp': 245, 'qualified': 14755}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=14755 p50=46.2877 p90=52.925 p99=70.4559 p99.9=76.0357 max=92.7576 mean=36.8453
T_fw_addon_nohold: n=14755 p50=9.5958 p90=12.4227 p99=15.2983 p99.9=19.1782 max=22.4684 mean=9.0282
T_fw_addon_sse: n=10325 p50=49.2884 p90=53.8557 p99=70.913 p99.9=80.3693 max=92.7576 mean=48.7483
T_fw_addon_json: n=4430 p50=9.6767 p90=12.7549 p99=15.6711 p99.9=19.6726 max=21.6505 mean=9.103
T_addon_first_sse: n=10325 p50=9.4886 p90=12.7724 p99=26.7535 p99.9=33.1686 max=53.1066 mean=9.1962
T_addon_total_sse: n=10325 p50=9.5542 p90=12.2107 p99=15.1926 p99.9=19.0783 max=22.4684 mean=8.9961
T_addon_total_json: n=4430 p50=9.6767 p90=12.7549 p99=15.6711 p99.9=19.6726 max=21.6505 mean=9.103
T_release_lag_max: n=10325 p50=49.2884 p90=53.8557 p99=70.913 p99.9=80.3693 max=92.7576 mean=48.7483
client_ttft_sse: n=10325 p50=159.5375 p90=162.8274 p99=176.7647 p99.9=183.1876 max=203.1275 mean=159.2406
lateness: n=15000 p50=0.0903 p90=0.1035 p99=0.1185 p99.9=0.148 max=3.0287 mean=0.0906
loadgen: [{'vm': 'rv-pbu-lg-3', 'busy_max': 15.2, 'busy_mean': 4.6, 'late_max_us': 3028, 'conn_opens': 149, 'max_inflight': 137}, {'vm': 'rv-pbu-lg-4', 'busy_max': 17.8, 'busy_mean': 4.5, 'late_max_us': 275, 'conn_opens': 148, 'max_inflight': 137}]
wire per client request: {'client_to_gw': 8388.4, 'gw_to_client': 56354.2, 'gw_to_provider': 9212.7, 'provider_to_gw': 57021.7}
gateway cores 1.804 cpu-ms/req 36.195 (workers 32.128, owners 4.058, redis 0.211)
worker util {'n': 18, 'min': 0.068, 'median': 0.09, 'max': 0.113, 'all_sorted': [0.068, 0.072, 0.077, 0.077, 0.078, 0.081, 0.081, 0.083, 0.085, 0.09, 0.093, 0.096, 0.097, 0.098, 0.098, 0.103, 0.112, 0.113]}; per-core schedstat max 0.092 mean 0.078; procstat max 0.124
gpu: {'0': {'samples': 298, 'sm_mean': 7.9, 'sm_max': 22.0, 'mem_mean': 1.4, 'fb_mb_max': 434.0}, '1': {'samples': 298, 'sm_mean': 7.5, 'sm_max': 19.0, 'mem_mean': 1.4, 'fb_mb_max': 434.0}}
rss max total by role (MB): {'redis': 301.8, 'launcher': 59.3, 'owner': 3065.5, 'worker': 3631.3}; fds {'redis': 0, 'launcher': 7, 'owner': 126, 'worker': 994}
W t_input_ns: {'n': 14985, 'mean_ms': 7.632, 'p50_ms': 8.3558, 'p90_ms': 10.1581, 'p99_ms': 12.5174, 'p99.9_ms': 16.3185, 'max_cum_ms': 28.4715}
W t_admit_ns: {'n': 14984, 'mean_ms': 0.0949, 'p50_ms': 0.0906, 'p90_ms': 0.1142, 'p99_ms': 0.1505, 'p99.9_ms': 0.684, 'max_cum_ms': 2.8912}
W t_tokenize_ns: {'n': 14984, 'mean_ms': 2.8552, 'p50_ms': 2.8672, 'p90_ms': 4.2926, 'p99_ms': 4.8824, 'p99.9_ms': 6.1932, 'max_cum_ms': 7.6311}
W t_det_scan_ns: {'n': 14984, 'mean_ms': 0.0995, 'p50_ms': 0.1009, 'p90_ms': 0.1213, 'p99_ms': 0.1505, 'p99.9_ms': 0.1915, 'max_cum_ms': 1.4745}
W t_guard_wait_ns: {'n': 14985, 'mean_ms': 4.2096, 'p50_ms': 4.7514, 'p90_ms': 5.1446, 'p99_ms': 7.2417, 'p99.9_ms': 11.2067, 'max_cum_ms': 22.9596}
W guard_owner_rtt_ns: {'n': 14985, 'mean_ms': 4.3811, 'p50_ms': 5.0135, 'p90_ms': 5.4067, 'p99_ms': 7.4383, 'p99.9_ms': 10.5513, 'max_cum_ms': 22.3906}
W guard_queue_ns: {'n': 14985, 'mean_ms': 0.1096, 'p50_ms': 0.108, 'p90_ms': 0.1275, 'p99_ms': 0.1546, 'p99.9_ms': 0.1874, 'max_cum_ms': 4.1818}
W guard_exec_ns: {'n': 14985, 'mean_ms': 3.5784, 'p50_ms': 4.2271, 'p90_ms': 4.4237, 'p99_ms': 6.4553, 'p99.9_ms': 6.5864, 'max_cum_ms': 9.4896}
W dispatch_headers_ns: {'n': 14747, 'mean_ms': 1521.1818, 'p50_ms': 149.9464, 'p90_ms': 5872.0256, 'p99_ms': 7885.2915, 'p99.9_ms': 8153.727, 'max_cum_ms': 8136.6871}
W release_lag_ns: {'n': 2289913, 'mean_ms': 19.8884, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.0619}
W holdback_wait_ns: {'n': 2231705, 'mean_ms': 20.3407, 'p50_ms': 20.054, 'p90_ms': 20.054, 'p99_ms': 40.108, 'p99.9_ms': 40.108, 'max_cum_ms': 100.0169}
W release_processing_ns: {'n': 2289913, 'mean_ms': 0.0648, 'p50_ms': 0.0637, 'p90_ms': 0.0855, 'p99_ms': 0.1142, 'p99.9_ms': 0.1464, 'max_cum_ms': 2.3344}
W t_finalize_ns: {'n': 14764, 'mean_ms': 0.2687, 'p50_ms': 0.2642, 'p90_ms': 0.3338, 'p99_ms': 0.3953, 'p99.9_ms': 0.4362, 'max_cum_ms': 1.0753}
W loop_lag_ns: {'n': 53642, 'mean_ms': 0.7023, 'p50_ms': 0.0234, 'p90_ms': 1.2534, 'p99_ms': 7.0451, 'p99.9_ms': 9.2406, 'max_cum_ms': 19.6193}
W guard_windows_per_request: {'n': 14984, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_exec_ns: {'n': 14976, 'mean_ms': 3.5784, 'p50_ms': 4.2271, 'p90_ms': 4.4237, 'p99_ms': 6.4553, 'p99.9_ms': 6.5864, 'max_cum_ms': 9.4896}
O guard_batch_windows: {'n': 14976, 'mean_ms': 0.0, 'p50_ms': 0.0, 'p90_ms': 0.0, 'p99_ms': 0.0, 'p99.9_ms': 0.0, 'max_cum_ms': 0.0}
O guard_queue_ns: {'n': 14976, 'mean_ms': 0.1096, 'p50_ms': 0.108, 'p90_ms': 0.1275, 'p99_ms': 0.1546, 'p99.9_ms': 0.1874, 'max_cum_ms': 4.1818}
worker counts: {'admitted': 14984, 'audit_enqueued': 29749, 'audit_written': 29749, 'background_round_trips': 10729, 'disposition_ALLOW': 14742, 'disposition_BLOCK': 243, 'guard_windows': 24738, 'lease_granted_tokens{org="org-a"}': 14175360, 'lease_refills': 69, 'provider_calls': 14742, 'provider_connections_opened': 2858, 'quota_admitted_tokens{org="org-a"}': 14222527, 'requests_by_round_trips{n="0"}': 14915, 'requests_by_round_trips{n="1"}': 69, 'shared_state_round_trips': 69}
owner counts: {'guard_batches': 14976, 'guard_windows': 24723, 'owner_requests': 14976, 'owner_windows': 24723}
gauges: {'audit_queue_bound': [181], 'audit_completeness_ratio': [1.0], 'audit_dropped': [0], 'inflight_cap': [786418.0], 'guard_tokens_per_s': [26802.292245528068, 26961.884746460342], 'input_queue_cap': [70.0, 71.0], 'guard_queue_cap_tokens': [26802.0, 26961.0]} owners: [{'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 241220, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 241220.63020975262}, {'owner_clients': 9, 'owner_queued_tokens': 0, 'owner_queue_cap_tokens': 242656, 'guard_queue_items': 0, 'guard_queue_windows': 0, 'guard_queue_oldest_age_seconds': 0.0, 'guard_tokens_per_s': 242656.96271814307}]
notes: []
