# uz1-175: strict FAIL | load-knee FAIL (sut, units=1, rate=175)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 52500 (175.0/s) qualified 51574 (171.91/s) FP-blocks 926 (0.01764) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=51574 p50=47.8095 p90=55.5822 p99=72.4864 p99.9=80.8132 max=101.3928 mean=38.9933
T_fw_addon_nohold: n=51574 p50=11.0475 p90=14.9576 p99=20.096 p99.9=26.3546 max=45.0163 mean=10.8256
T_fw_addon_sse: n=36111 p50=50.9545 p90=57.2902 p99=73.3672 p99.9=86.4143 max=101.3928 mean=51.0314
T_fw_addon_json: n=15463 p50=11.0771 p90=15.0859 p99=20.2162 p99.9=26.7673 max=45.0163 mean=10.8807
T_addon_first_sse: n=36111 p50=10.9722 p90=15.2929 p99=30.2866 p99.9=36.617 max=56.4443 mean=11.0611
T_addon_total_sse: n=36111 p50=11.0345 p90=14.9065 p99=20.0493 p99.9=26.1281 max=40.3873 mean=10.802
T_addon_total_json: n=15463 p50=11.0771 p90=15.0859 p99=20.2162 p99.9=26.7673 max=45.0163 mean=10.8807
T_release_lag_max: n=36111 p50=50.9545 p90=57.2902 p99=73.3672 p99.9=86.4143 max=101.3928 mean=51.0314
lateness: n=52500 p50=0.0878 p90=0.0979 p99=0.1082 p99.9=0.1229 max=0.3152 mean=0.0877
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 5.8, 'busy_mean': 5.4, 'late_max_us': 547, 'conn_opens': 310, 'max_inflight': 300}, {'vm': 'rv-pbf-lg-2', 'busy_max': 5.8, 'busy_mean': 5.3, 'late_max_us': 284, 'conn_opens': 309, 'max_inflight': 299}, {'vm': 'rv-pbf-lg-3', 'busy_max': 5.8, 'busy_mean': 5.4, 'late_max_us': 222, 'conn_opens': 310, 'max_inflight': 299}]  provider_cpu_busy_max: 24.629498396251826
gateway cores total 6.0 cpu-ms/req {'gateway': 34.405, 'workers': 30.421, 'owners': 3.982}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.694, 'owner0': 0.335, 'owner1': 0.36, 'redis': 0.001, 'worker': 5.306} worker util max 0.363 per-core max 0.273 mean 0.254 gpu {'0': {'n': 298, 'sm_mean': 25.0, 'sm_p95': 39.0, 'sm_max': 52.0}, '1': {'n': 298, 'sm_mean': 28.3, 'sm_p95': 42.0, 'sm_max': 59.0}} t_input_p99 15.9252 per-worker admitted {'n': 18, 'min': 1727, 'max': 3727, 'mean': 2913.2, 'max_over_mean': 1.279, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
W t_input_ns: {'n': 52440, 'mean_ms': 8.2566, 'p50_ms': 8.7163, 'p90_ms': 11.7309, 'p99_ms': 15.9252, 'p99.9_ms': 20.8404, 'max_cum_ms': 29.5445}
W t_tokenize_ns: {'n': 52438, 'mean_ms': 3.1969, 'p50_ms': 3.1949, 'p90_ms': 5.079, 'p99_ms': 6.0621, 'p99.9_ms': 6.914, 'max_cum_ms': 7.9125}
W t_guard_wait_ns: {'n': 52440, 'mean_ms': 4.4536, 'p50_ms': 4.8169, 'p90_ms': 6.8485, 'p99_ms': 10.8134, 'p99.9_ms': 15.2699, 'max_cum_ms': 20.2375}
W guard_owner_rtt_ns: {'n': 52439, 'mean_ms': 4.6251, 'p50_ms': 5.0135, 'p90_ms': 6.914, 'p99_ms': 10.4202, 'p99.9_ms': 14.0902, 'max_cum_ms': 18.7309}
W guard_queue_ns: {'n': 52439, 'mean_ms': 0.2443, 'p50_ms': 0.1009, 'p90_ms': 0.1628, 'p99_ms': 3.4243, 'p99.9_ms': 6.6519, 'max_cum_ms': 10.5603}
W guard_exec_ns: {'n': 52439, 'mean_ms': 3.5624, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.783, 'max_cum_ms': 9.0647}
W t_admit_ns: {'n': 52438, 'mean_ms': 0.0934, 'p50_ms': 0.0865, 'p90_ms': 0.1111, 'p99_ms': 0.1464, 'p99.9_ms': 1.0568, 'max_cum_ms': 6.9353}
W release_processing_ns: {'n': 8056845, 'mean_ms': 0.064, 'p50_ms': 0.0627, 'p90_ms': 0.0845, 'p99_ms': 0.1132, 'p99.9_ms': 0.1444, 'max_cum_ms': 3.4908}
W loop_lag_ns: {'n': 53590, 'mean_ms': 0.9023, 'p50_ms': 0.0129, 'p90_ms': 3.7847, 'p99_ms': 9.8959, 'p99.9_ms': 12.5174, 'max_cum_ms': 20.7206}
W audit_batch_write_ns: {'n': 103798, 'mean_ms': 1.2283, 'p50_ms': 1.0363, 'p90_ms': 1.4336, 'p99_ms': 6.3898, 'p99.9_ms': 12.1242, 'max_cum_ms': 27.9887}
W counts: {"admitted": 52438, "audit_enqueued": 104103, "audit_written": 104103, "background_round_trips": 10718, "disposition_ALLOW": 51514, "disposition_BLOCK": 926, "guard_windows": 86370, "lease_refills": 241, "provider_calls": 51514, "provider_connections_opened": 9863, "requests_by_round_trips{n=\"0\"}": 52197, "requests_by_round_trips{n=\"1\"}": 241, "shared_state_round_trips": 241}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.238}, "per_proc_util": {"nginx": [0.0, 0.012, 0.013, 0.013, 0.014, 0.014, 0.015, 0.015, 0.015, 0.015, 0.015, 0.015, 0.016, 0.016, 0.017, 0.017, 0.017]}, "per_core_util": {"max": 0.017, "mean": 0.015, "sum": 0.24, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.51} nginx cpu-ms/req 1.367
redis: ops/s 944.8 ops/req 5.399 cpu cores 0.01 clients 253 mem 667.6MB ping(us) {'n': 28097, 'p50_us': 563.9, 'p90_us': 595.4, 'p99_us': 695.7, 'p99.9_us': 2702.6, 'max_us': 5025.3, 'mean_us': 575.6}
redis cmdstats: {'ping': {'calls_per_s': 93.4, 'usec_per_call': 0.09}, 'hgetall': {'calls_per_s': 358.8, 'usec_per_call': 0.2}, 'decrby': {'calls_per_s': 0.8, 'usec_per_call': 0.42}, 'mget': {'calls_per_s': 143.5, 'usec_per_call': 0.37}, 'xadd': {'calls_per_s': 346.7, 'usec_per_call': 3.74}, 'info': {'calls_per_s': 0.0, 'usec_per_call': 130.0}, 'get': {'calls_per_s': 0.8, 'usec_per_call': 0.51}, 'evalsha': {'calls_per_s': 0.8, 'usec_per_call': 10.69}}
wire: {'requests_in_window': 52500, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56362.7, 'client_side_to_unit': 8102.4, 'unit_to_provider': 8800.7, 'provider_to_unit': 57056.9, 'unit_to_redis': 5455.3, 'redis_to_unit': 308.9}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56489.2, 'clients_to_edge': 8252.0}, 'olg_resp_body_bytes_mean': {'sse': 67581.1, 'json': 1421.6}}
