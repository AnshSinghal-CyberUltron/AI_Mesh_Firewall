# ub1-032: strict FAIL | load-knee PASS (sut, units=1, rate=32)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 9600 (32.0/s) qualified 9456 (31.52/s) FP-blocks 144 (0.015) infra 0 (0.0) drops 0 safety 0
infra reasons: {}
infra detail: {}

T_fw_addon: n=9456 p50=46.6769 p90=52.9295 p99=70.8781 p99.9=76.3903 max=92.1094 mean=37.1565
T_fw_addon_nohold: n=9456 p50=9.988 p90=12.7331 p99=15.2227 p99.9=18.7536 max=21.4145 mean=9.3975
T_fw_addon_sse: n=6609 p50=49.7472 p90=53.6242 p99=71.2471 p99.9=87.1073 max=92.1094 mean=49.1143
T_fw_addon_json: n=2847 p50=10.0183 p90=12.7843 p99=15.0638 p99.9=18.2909 max=18.692 mean=9.3979
T_addon_first_sse: n=6609 p50=9.9325 p90=13.204 p99=30.0157 p99.9=33.5317 max=66.6274 mean=9.7052
T_addon_total_sse: n=6609 p50=9.9726 p90=12.6969 p99=15.2367 p99.9=19.6595 max=21.4145 mean=9.3974
T_addon_total_json: n=2847 p50=10.0183 p90=12.7843 p99=15.0638 p99.9=18.2909 max=18.692 mean=9.3979
T_release_lag_max: n=6609 p50=49.7472 p90=53.6242 p99=71.2471 p99.9=87.1073 max=92.1094 mean=49.1143
lateness: n=9600 p50=0.0863 p90=0.0972 p99=0.1115 p99.9=0.1345 max=0.2111 mean=0.0871
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 15.3, 'busy_mean': 3.6, 'late_max_us': 225, 'conn_opens': 63, 'max_inflight': 61}, {'vm': 'rv-pbf-lg-2', 'busy_max': 14.3, 'busy_mean': 3.7, 'late_max_us': 167, 'conn_opens': 63, 'max_inflight': 61}, {'vm': 'rv-pbf-lg-3', 'busy_max': 13.1, 'busy_mean': 3.7, 'late_max_us': 219, 'conn_opens': 63, 'max_inflight': 61}]  provider_cpu_busy_max: 19.073543877686117
gateway cores total 1.17 cpu-ms/req {'gateway': 36.726, 'workers': 32.64, 'owners': 4.073}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.13, 'owner0': 0.068, 'owner1': 0.062, 'redis': 0.001, 'worker': 1.041} worker util max 0.093 per-core max 0.069 mean 0.052 gpu {'0': {'n': 299, 'sm_mean': 5.2, 'sm_p95': 11.0, 'sm_max': 17.0}, '1': {'n': 299, 'sm_mean': 4.7, 'sm_p95': 10.0, 'sm_max': 17.0}} t_input_p99 12.2552 per-worker admitted {'n': 18, 'min': 294, 'max': 971, 'mean': 532.4, 'max_over_mean': 1.824, 'sheds_per_worker': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
W t_input_ns: {'n': 9584, 'mean_ms': 7.3125, 'p50_ms': 8.0282, 'p90_ms': 9.8959, 'p99_ms': 12.2552, 'p99.9_ms': 15.532, 'max_cum_ms': 29.3969}
W t_tokenize_ns: {'n': 9584, 'mean_ms': 2.6501, 'p50_ms': 2.7034, 'p90_ms': 4.0796, 'p99_ms': 4.6858, 'p99.9_ms': 5.931, 'max_cum_ms': 7.1509}
W t_guard_wait_ns: {'n': 9584, 'mean_ms': 4.1481, 'p50_ms': 4.7514, 'p90_ms': 5.079, 'p99_ms': 7.1762, 'p99.9_ms': 11.0756, 'max_cum_ms': 13.5719}
W guard_owner_rtt_ns: {'n': 9584, 'mean_ms': 4.3145, 'p50_ms': 4.948, 'p90_ms': 5.2756, 'p99_ms': 7.3728, 'p99.9_ms': 10.1581, 'max_cum_ms': 16.225}
W guard_queue_ns: {'n': 9584, 'mean_ms': 0.1019, 'p50_ms': 0.0998, 'p90_ms': 0.1213, 'p99_ms': 0.1444, 'p99.9_ms': 0.1833, 'max_cum_ms': 1.0067}
W guard_exec_ns: {'n': 9584, 'mean_ms': 3.5697, 'p50_ms': 4.2271, 'p90_ms': 4.4237, 'p99_ms': 6.4553, 'p99.9_ms': 6.5864, 'max_cum_ms': 9.1313}
W t_admit_ns: {'n': 9584, 'mean_ms': 0.0911, 'p50_ms': 0.0855, 'p90_ms': 0.1019, 'p99_ms': 0.1275, 'p99.9_ms': 1.0732, 'max_cum_ms': 7.6442}
W release_processing_ns: {'n': 1476751, 'mean_ms': 0.0625, 'p50_ms': 0.0627, 'p90_ms': 0.0773, 'p99_ms': 0.0988, 'p99.9_ms': 0.1224, 'max_cum_ms': 0.8203}
W loop_lag_ns: {'n': 53640, 'mean_ms': 0.6691, 'p50_ms': 0.0041, 'p90_ms': 1.237, 'p99_ms': 6.5208, 'p99.9_ms': 9.2406, 'max_cum_ms': 13.4394}
W audit_batch_write_ns: {'n': 19037, 'mean_ms': 0.9911, 'p50_ms': 0.9462, 'p90_ms': 1.1878, 'p99_ms': 1.4008, 'p99.9_ms': 7.1762, 'max_cum_ms': 14.074}
W counts: {"admitted": 9584, "audit_enqueued": 19040, "audit_written": 19040, "background_round_trips": 10728, "disposition_ALLOW": 9440, "disposition_BLOCK": 144, "guard_windows": 15878, "lease_refills": 48, "provider_calls": 9440, "provider_connections_opened": 1202, "requests_by_round_trips{n=\"0\"}": 9536, "requests_by_round_trips{n=\"1\"}": 48, "shared_state_round_trips": 48}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.038}, "per_proc_util": {"nginx": [0.0, 0.001, 0.002, 0.002, 0.002, 0.002, 0.002, 0.002, 0.002, 0.002, 0.002, 0.003, 0.003, 0.003, 0.003, 0.003, 0.004]}, "per_core_util": {"max": 0.004, "mean": 0.003, "sum": 0.05, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.08} nginx cpu-ms/req 1.179
redis: ops/s 660.6 ops/req 20.643 cpu cores 0.004 clients 253 mem 204.3MB ping(us) {'n': 28297, 'p50_us': 496.3, 'p90_us': 573.9, 'p99_us': 640.3, 'p99.9_us': 1219.0, 'max_us': 3295.2, 'mean_us': 511.4}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 141.0}, 'hgetall': {'calls_per_s': 359.0, 'usec_per_call': 0.19}, 'evalsha': {'calls_per_s': 0.2, 'usec_per_call': 12.9}, 'xadd': {'calls_per_s': 63.4, 'usec_per_call': 3.39}, 'decrby': {'calls_per_s': 0.2, 'usec_per_call': 0.4}, 'mget': {'calls_per_s': 143.6, 'usec_per_call': 0.34}, 'get': {'calls_per_s': 0.2, 'usec_per_call': 0.65}, 'ping': {'calls_per_s': 94.1, 'usec_per_call': 0.09}}
wire: {'requests_in_window': 9600, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56563.8, 'client_side_to_unit': 9127.2, 'unit_to_provider': 9711.3, 'provider_to_unit': 57245.1, 'unit_to_redis': 5921.5, 'redis_to_unit': 578.0}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56685.5, 'clients_to_edge': 9475.0}, 'olg_resp_body_bytes_mean': {'sse': 67972.8, 'json': 1445.9}}
