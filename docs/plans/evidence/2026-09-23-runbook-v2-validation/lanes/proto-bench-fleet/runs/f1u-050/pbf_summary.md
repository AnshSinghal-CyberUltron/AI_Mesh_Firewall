# f1u-050: strict FAIL | load-knee PASS (sut, units=1, rate=50)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 15000 (50.0/s) qualified 14797 (49.32/s) FP-blocks 203 (0.01353) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=14797 p50=10.8784 p90=15.8984 p99=54.0587 p99.9=71.3283 max=90.9917 mean=13.2262
T_fw_addon_nohold: n=14797 p50=10.5683 p90=13.6927 p99=16.2372 p99.9=20.1379 max=24.309 mean=10.1092
T_fw_addon_sse: n=10352 p50=10.943 p90=32.0752 p99=55.0053 p99.9=72.8945 max=90.9917 mean=14.5297
T_fw_addon_json: n=4445 p50=10.7008 p90=13.7805 p99=16.139 p99.9=19.4996 max=22.0527 mean=10.1906
T_addon_first_sse: n=10352 p50=10.4631 p90=13.8228 p99=29.5485 p99.9=33.549 max=51.0472 mean=10.3102
T_addon_total_sse: n=10352 p50=10.5307 p90=13.6463 p99=16.2885 p99.9=20.2849 max=24.309 mean=10.0743
T_addon_total_json: n=4445 p50=10.7008 p90=13.7805 p99=16.139 p99.9=19.4996 max=22.0527 mean=10.1906
T_release_lag_max: n=1055 p50=50.1143 p90=54.9622 p99=72.8945 p99.9=90.8944 max=90.9917 mean=49.6298
lateness: n=15000 p50=0.0918 p90=0.1072 p99=0.1271 p99.9=0.151 max=0.2287 mean=0.0928
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 6.7, 'busy_mean': 4.0, 'late_max_us': 305, 'conn_opens': 98, 'max_inflight': 93}, {'vm': 'rv-pbf-lg-2', 'busy_max': 4.2, 'busy_mean': 3.9, 'late_max_us': 255, 'conn_opens': 98, 'max_inflight': 93}, {'vm': 'rv-pbf-lg-3', 'busy_max': 4.1, 'busy_mean': 3.9, 'late_max_us': 235, 'conn_opens': 99, 'max_inflight': 93}]  provider_cpu_busy_max: 28.89987550366593
gateway cores total 1.81 cpu-ms/req {'gateway': 36.407, 'workers': 32.303, 'owners': 4.096}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.204, 'owner0': 0.106, 'owner1': 0.099, 'redis': 0.001, 'worker': 1.61} worker util max 0.122 per-core max 0.094 mean 0.078 gpu {'0': {'n': 298, 'sm_mean': 8.1, 'sm_p95': 15.0, 'sm_max': 22.0}, '1': {'n': 298, 'sm_mean': 7.4, 'sm_p95': 15.0, 'sm_max': 24.0}} t_input_p99 12.6484 per-worker admitted {'n': 18, 'min': 472, 'max': 1197, 'mean': 832.6, 'max_over_mean': 1.438, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
W t_input_ns: {'n': 14988, 'mean_ms': 7.7307, 'p50_ms': 8.4541, 'p90_ms': 10.2892, 'p99_ms': 12.6484, 'p99.9_ms': 15.6631, 'max_cum_ms': 35.9043}
W t_tokenize_ns: {'n': 14987, 'mean_ms': 2.9262, 'p50_ms': 2.9655, 'p90_ms': 4.4237, 'p99_ms': 5.0135, 'p99.9_ms': 6.1932, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 14988, 'mean_ms': 4.2388, 'p50_ms': 4.8169, 'p90_ms': 5.2101, 'p99_ms': 7.2417, 'p99.9_ms': 11.3377, 'max_cum_ms': 29.2545}
W guard_owner_rtt_ns: {'n': 14988, 'mean_ms': 4.4133, 'p50_ms': 5.0135, 'p90_ms': 5.4067, 'p99_ms': 7.4383, 'p99.9_ms': 10.5513, 'max_cum_ms': 27.7519}
W guard_queue_ns: {'n': 14988, 'mean_ms': 0.1114, 'p50_ms': 0.1101, 'p90_ms': 0.1285, 'p99_ms': 0.1505, 'p99.9_ms': 0.1915, 'max_cum_ms': 16.1001}
W guard_exec_ns: {'n': 14988, 'mean_ms': 3.5929, 'p50_ms': 4.2271, 'p90_ms': 4.4237, 'p99_ms': 6.4553, 'p99.9_ms': 6.5864, 'max_cum_ms': 8.1548}
W t_admit_ns: {'n': 14987, 'mean_ms': 0.0985, 'p50_ms': 0.0916, 'p90_ms': 0.1152, 'p99_ms': 0.1485, 'p99.9_ms': 1.0732, 'max_cum_ms': 21.7487}
W release_processing_ns: {'n': 2296961, 'mean_ms': 0.0646, 'p50_ms': 0.0637, 'p90_ms': 0.0845, 'p99_ms': 0.1121, 'p99.9_ms': 0.1423, 'max_cum_ms': 5.7792}
W loop_lag_ns: {'n': 53610, 'mean_ms': 0.7562, 'p50_ms': 0.018, 'p90_ms': 1.2698, 'p99_ms': 7.766, 'p99.9_ms': 9.7649, 'max_cum_ms': 21.4172}
W audit_batch_write_ns: {'n': 29785, 'mean_ms': 1.0554, 'p50_ms': 0.9871, 'p90_ms': 1.2698, 'p99_ms': 1.7449, 'p99.9_ms': 7.9626, 'max_cum_ms': 40.9418}
W counts: {"admitted": 14987, "audit_enqueued": 29793, "audit_written": 29793, "background_round_trips": 10722, "disposition_ALLOW": 14785, "disposition_BLOCK": 203, "guard_windows": 24680, "lease_refills": 68, "provider_calls": 14785, "provider_connections_opened": 3012, "requests_by_round_trips{n=\"0\"}": 14919, "requests_by_round_trips{n=\"1\"}": 68, "shared_state_round_trips": 68}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.069}, "per_proc_util": {"nginx": [0.0, 0.003, 0.004, 0.004, 0.004, 0.004, 0.004, 0.004, 0.004, 0.004, 0.004, 0.004, 0.004, 0.005, 0.005, 0.005, 0.006]}, "per_core_util": {"max": 0.007, "mean": 0.005, "sum": 0.07, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.23} nginx cpu-ms/req 1.386
redis: ops/s 697.1 ops/req 13.942 cpu cores 0.004 clients 255 mem 460.6MB ping(us) {'n': 28461, 'p50_us': 433.1, 'p90_us': 464.6, 'p99_us': 566.3, 'p99.9_us': 2240.5, 'max_us': 4962.3, 'mean_us': 445.2}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 132.0}, 'ping': {'calls_per_s': 94.7, 'usec_per_call': 0.1}, 'evalsha': {'calls_per_s': 0.2, 'usec_per_call': 11.66}, 'xadd': {'calls_per_s': 99.3, 'usec_per_call': 3.51}, 'mget': {'calls_per_s': 143.5, 'usec_per_call': 0.37}, 'hgetall': {'calls_per_s': 358.9, 'usec_per_call': 0.2}, 'get': {'calls_per_s': 0.2, 'usec_per_call': 0.63}, 'decrby': {'calls_per_s': 0.2, 'usec_per_call': 0.41}}
wire: {'requests_in_window': 15000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56299.2, 'client_side_to_unit': 8659.4, 'unit_to_provider': 9565.5, 'provider_to_unit': 56996.1, 'unit_to_redis': 5735.8, 'redis_to_unit': 460.7}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56426.3, 'clients_to_edge': 8939.5}, 'olg_resp_body_bytes_mean': {'sse': 67136.6, 'json': 1435.7}}
