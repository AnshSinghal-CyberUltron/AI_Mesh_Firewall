# smoke-1u-020b: strict FAIL | load-knee PASS (sut, units=1, rate=20)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 600 (20.0/s) qualified 583 (19.43/s) FP-blocks 17 (0.02833) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=583 p50=10.5105 p90=14.0173 p99=51.4365 p99.9=53.871 max=53.871 mean=12.0141
T_fw_addon_nohold: n=583 p50=10.305 p90=13.3761 p99=14.9853 p99.9=15.6071 max=15.6071 mean=9.7657
T_fw_addon_sse: n=406 p50=10.4788 p90=15.0192 p99=51.5056 p99.9=53.871 max=53.871 mean=12.8461
T_fw_addon_json: n=177 p50=10.5746 p90=13.5373 p99=14.8479 p99.9=15.1764 max=15.1764 mean=10.1057
T_addon_first_sse: n=406 p50=10.0853 p90=13.5022 p99=26.4615 p99.9=33.5744 max=33.5744 mean=9.8271
T_addon_total_sse: n=406 p50=10.1422 p90=13.3761 p99=14.9853 p99.9=15.6071 max=15.6071 mean=9.6175
T_addon_total_json: n=177 p50=10.5746 p90=13.5373 p99=14.8479 p99.9=15.1764 max=15.1764 mean=10.1057
T_release_lag_max: n=32 p50=48.2759 p90=51.5873 p99=53.871 p99.9=53.871 max=53.871 mean=47.6953
lateness: n=600 p50=0.0862 p90=0.0969 p99=0.1119 p99.9=0.2124 max=0.2124 mean=0.087
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 4.7, 'busy_mean': 3.4, 'late_max_us': 215, 'conn_opens': 37, 'max_inflight': 37}, {'vm': 'rv-pbf-lg-2', 'busy_max': 3.7, 'busy_mean': 3.5, 'late_max_us': 212, 'conn_opens': 37, 'max_inflight': 37}, {'vm': 'rv-pbf-lg-3', 'busy_max': 4.0, 'busy_mean': 3.5, 'late_max_us': 189, 'conn_opens': 37, 'max_inflight': 37}]  provider_cpu_busy_max: 11.719904849120589
gateway cores total 0.83 cpu-ms/req {'gateway': 42.68, 'workers': 38.301, 'owners': 4.357}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.084, 'owner0': 0.046, 'owner1': 0.038, 'redis': 0.001, 'worker': 0.741} worker util max 0.067 per-core max 0.106 mean 0.043 gpu {'0': {'n': 31, 'sm_mean': 3.1, 'sm_p95': 7.0, 'sm_max': 11.0}, '1': {'n': 31, 'sm_mean': 2.7, 'sm_p95': 6.0, 'sm_max': 7.0}} t_input_p99 12.1242
W t_input_ns: {'n': 588, 'mean_ms': 7.5906, 'p50_ms': 8.4541, 'p90_ms': 9.8959, 'p99_ms': 12.1242, 'p99.9_ms': 12.2552, 'max_cum_ms': 21.0232}
W t_tokenize_ns: {'n': 588, 'mean_ms': 2.6563, 'p50_ms': 2.7689, 'p90_ms': 3.9485, 'p99_ms': 4.3581, 'p99.9_ms': 4.4892, 'max_cum_ms': 6.323}
W t_guard_wait_ns: {'n': 588, 'mean_ms': 4.4051, 'p50_ms': 5.0135, 'p90_ms': 5.2756, 'p99_ms': 7.3728, 'p99.9_ms': 8.8474, 'max_cum_ms': 10.5208}
W guard_owner_rtt_ns: {'n': 588, 'mean_ms': 4.5895, 'p50_ms': 5.2101, 'p90_ms': 5.4723, 'p99_ms': 7.5694, 'p99.9_ms': 8.9784, 'max_cum_ms': 10.5922}
W guard_queue_ns: {'n': 588, 'mean_ms': 0.1163, 'p50_ms': 0.1142, 'p90_ms': 0.1321, 'p99_ms': 0.1526, 'p99.9_ms': 0.1628, 'max_cum_ms': 0.836}
W guard_exec_ns: {'n': 588, 'mean_ms': 3.7581, 'p50_ms': 4.3581, 'p90_ms': 4.5548, 'p99_ms': 6.6519, 'p99.9_ms': 6.7174, 'max_cum_ms': 7.2794}
W t_admit_ns: {'n': 588, 'mean_ms': 0.0908, 'p50_ms': 0.0896, 'p90_ms': 0.1019, 'p99_ms': 0.1234, 'p99.9_ms': 0.1306, 'max_cum_ms': 8.8468}
W release_processing_ns: {'n': 97122, 'mean_ms': 0.0662, 'p50_ms': 0.066, 'p90_ms': 0.0835, 'p99_ms': 0.108, 'p99.9_ms': 0.1341, 'max_cum_ms': 0.6502}
W loop_lag_ns: {'n': 5400, 'mean_ms': 0.5962, 'p50_ms': 0.0079, 'p90_ms': 1.0895, 'p99_ms': 5.6033, 'p99.9_ms': 6.3898, 'max_cum_ms': 10.1726}
W audit_batch_write_ns: {'n': 1174, 'mean_ms': 1.0569, 'p50_ms': 0.9953, 'p90_ms': 1.2534, 'p99_ms': 2.1463, 'p99.9_ms': 5.6689, 'max_cum_ms': 6.2486}
W counts: {"admitted": 588, "audit_enqueued": 1175, "audit_written": 1175, "background_round_trips": 1080, "disposition_ALLOW": 571, "disposition_BLOCK": 17, "guard_windows": 989, "provider_calls": 571, "provider_connections_opened": 96, "requests_by_round_trips{n=\"0\"}": 588}
edge: {"window_s": 31.0, "cores_by_role": {"nginx": 0.023}, "per_proc_util": {"nginx": [0.0, 0.001, 0.001, 0.001, 0.001, 0.001, 0.001, 0.001, 0.001, 0.002, 0.002, 0.002, 0.002, 0.002, 0.002, 0.002, 0.002]}, "per_core_util": {"max": 0.004, "mean": 0.002, "sum": 0.03, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.0} nginx cpu-ms/req 1.195
redis: ops/s 698.0 ops/req 34.902 cpu cores 0.004 clients 270 mem 15.8MB ping(us) {'n': 2838, 'p50_us': 475.5, 'p90_us': 505.4, 'p99_us': 606.6, 'p99.9_us': 1002.8, 'max_us': 1448.7, 'mean_us': 484.0}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 124.0}, 'ping': {'calls_per_s': 92.8, 'usec_per_call': 0.1}, 'xadd': {'calls_per_s': 39.0, 'usec_per_call': 3.46}, 'mget': {'calls_per_s': 161.7, 'usec_per_call': 0.35}, 'hgetall': {'calls_per_s': 404.2, 'usec_per_call': 0.19}, 'hello': {'calls_per_s': 0.2, 'usec_per_call': 2.71}}
wire: {'requests_in_window': 600, 'unit_ip_bytes_per_req': {'unit_to_client_side': 59078.4, 'client_side_to_unit': 10015.7, 'unit_to_provider': 10671.8, 'provider_to_unit': 59773.3, 'unit_to_redis': 6130.7, 'redis_to_unit': 768.7}, 'edge_ip_bytes_per_req': {'edge_to_clients': 59348.1, 'clients_to_edge': 10489.4}, 'olg_resp_body_bytes_mean': {'sse': 71065.9, 'json': 1407.2}}
