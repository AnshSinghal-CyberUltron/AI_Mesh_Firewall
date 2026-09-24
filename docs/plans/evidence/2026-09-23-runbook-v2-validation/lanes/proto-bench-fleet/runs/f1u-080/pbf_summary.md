# f1u-080: strict FAIL | load-knee FAIL (sut, units=1, rate=80)

checks: {'p99_T_fw_addon_lt_slo': False, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': False, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 24000 (80.0/s) qualified 23580 (78.6/s) FP-blocks 377 (0.01571) infra 43 (0.0017916666666666667) drops 0 safety 0
infra reasons: {'http_503': 43, 'incomplete': 43, 'unjoined': 43, 'disposition_missing': 43, 'stage_canon_missing': 43, 'stage_det_missing': 43, 'stage_sem_missing': 43, 'stage_resolve_missing': 43, 'stage_dispatch_missing': 43, 'stage_out_missing': 43, 'stage_audit_missing': 43}
infra detail: {'503 http_503 type=server_overloaded code=overloaded': 43}

T_fw_addon: n=23580 p50=10.6893 p90=16.1675 p99=54.0776 p99.9=71.5893 max=91.1211 mean=13.0888
T_fw_addon_nohold: n=23580 p50=10.4375 p90=13.626 p99=17.2089 p99.9=21.5407 max=25.9783 mean=9.9761
T_fw_addon_sse: n=16504 p50=10.7741 p90=33.3831 p99=55.4755 p99.9=72.3063 max=91.1211 mean=14.3884
T_fw_addon_json: n=7076 p50=10.5167 p90=13.7796 p99=17.4263 p99.9=21.7326 max=24.4604 mean=10.0575
T_addon_first_sse: n=16504 p50=10.3271 p90=13.758 p99=27.92 p99.9=34.2405 max=65.433 mean=10.1486
T_addon_total_sse: n=16504 p50=10.4082 p90=13.5394 p99=17.0465 p99.9=21.1789 max=25.9783 mean=9.9412
T_addon_total_json: n=7076 p50=10.5167 p90=13.7796 p99=17.4263 p99.9=21.7326 max=24.4604 mean=10.0575
T_release_lag_max: n=1685 p50=50.2176 p90=55.3904 p99=72.3063 p99.9=90.35 max=91.1211 mean=50.0305
lateness: n=24000 p50=0.0864 p90=0.0958 p99=0.1063 p99.9=0.1231 max=0.3333 mean=0.0858
loadgen: [{'vm': 'rv-pbf-lg-1', 'busy_max': 18.0, 'busy_mean': 4.2, 'late_max_us': 361, 'conn_opens': 152, 'max_inflight': 146}, {'vm': 'rv-pbf-lg-2', 'busy_max': 16.5, 'busy_mean': 4.3, 'late_max_us': 183, 'conn_opens': 153, 'max_inflight': 146}, {'vm': 'rv-pbf-lg-3', 'busy_max': 15.8, 'busy_mean': 4.2, 'late_max_us': 333, 'conn_opens': 151, 'max_inflight': 146}]  provider_cpu_busy_max: 21.283436626758167
gateway cores total 2.82 cpu-ms/req {'gateway': 35.318, 'workers': 31.237, 'owners': 4.076}
  rv-pbf-unit-2: cores {'launcher': 0.0, 'owner': 0.325, 'owner0': 0.165, 'owner1': 0.16, 'redis': 0.001, 'worker': 2.491} worker util max 0.172 per-core max 0.14 mean 0.12 gpu {'0': {'n': 299, 'sm_mean': 12.1, 'sm_p95': 22.0, 'sm_max': 29.0}, '1': {'n': 299, 'sm_mean': 12.0, 'sm_p95': 20.0, 'sm_max': 29.0}} t_input_p99 13.3038 per-worker admitted {'n': 18, 'min': 1055, 'max': 1733, 'mean': 1331.5, 'max_over_mean': 1.302, 'sheds_per_worker': [0, 0, 0, 1, 1, 1, 2, 2, 2, 2, 2, 3, 4, 4, 4, 4, 4, 7]}
W t_input_ns: {'n': 23925, 'mean_ms': 7.719, 'p50_ms': 8.4541, 'p90_ms': 10.5513, 'p99_ms': 13.3038, 'p99.9_ms': 17.9569, 'max_cum_ms': 36.429}
W t_tokenize_ns: {'n': 23967, 'mean_ms': 2.906, 'p50_ms': 2.9327, 'p90_ms': 4.4892, 'p99_ms': 5.2756, 'p99.9_ms': 6.4553, 'max_cum_ms': 8.9355}
W t_guard_wait_ns: {'n': 23925, 'mean_ms': 4.2576, 'p50_ms': 4.8169, 'p90_ms': 5.2756, 'p99_ms': 7.5694, 'p99.9_ms': 12.3863, 'max_cum_ms': 29.2545}
W guard_owner_rtt_ns: {'n': 23925, 'mean_ms': 4.4314, 'p50_ms': 5.0135, 'p90_ms': 5.4723, 'p99_ms': 7.7005, 'p99.9_ms': 12.2552, 'max_cum_ms': 27.7519}
W guard_queue_ns: {'n': 23925, 'mean_ms': 0.1116, 'p50_ms': 0.108, 'p90_ms': 0.1321, 'p99_ms': 0.1628, 'p99.9_ms': 0.9953, 'max_cum_ms': 16.1001}
W guard_exec_ns: {'n': 23925, 'mean_ms': 3.5987, 'p50_ms': 4.2271, 'p90_ms': 4.4892, 'p99_ms': 6.5864, 'p99.9_ms': 6.7174, 'max_cum_ms': 8.1548}
W t_admit_ns: {'n': 23967, 'mean_ms': 0.0933, 'p50_ms': 0.0876, 'p90_ms': 0.105, 'p99_ms': 0.1341, 'p99.9_ms': 1.0568, 'max_cum_ms': 21.7487}
W release_processing_ns: {'n': 3658443, 'mean_ms': 0.0641, 'p50_ms': 0.0632, 'p90_ms': 0.0814, 'p99_ms': 0.105, 'p99.9_ms': 0.1341, 'max_cum_ms': 5.7792}
W loop_lag_ns: {'n': 53560, 'mean_ms': 0.8098, 'p50_ms': 0.0083, 'p90_ms': 2.9983, 'p99_ms': 8.1592, 'p99.9_ms': 10.9445, 'max_cum_ms': 27.5291}
W audit_batch_write_ns: {'n': 47442, 'mean_ms': 1.0812, 'p50_ms': 0.9953, 'p90_ms': 1.2534, 'p99_ms': 3.7192, 'p99.9_ms': 9.1095, 'max_cum_ms': 40.9418}
W counts: {"admitted": 23967, "audit_enqueued": 47476, "audit_written": 47476, "background_round_trips": 10712, "disposition_ALLOW": 23548, "disposition_BLOCK": 377, "guard_windows": 39454, "lease_refills": 110, "provider_calls": 23548, "provider_connections_opened": 3499, "requests_by_round_trips{n=\"0\"}": 23857, "requests_by_round_trips{n=\"1\"}": 110, "shared_state_round_trips": 110, "shed{reason=\"guard_queue\"}": 43}
edge: {"window_s": 301.0, "cores_by_role": {"nginx": 0.09}, "per_proc_util": {"nginx": [0.0, 0.003, 0.004, 0.004, 0.004, 0.005, 0.005, 0.005, 0.006, 0.006, 0.006, 0.006, 0.007, 0.007, 0.007, 0.007, 0.008]}, "per_core_util": {"max": 0.009, "mean": 0.007, "sum": 0.11, "n": 16}, "nic": null, "restarted": [], "loadavg_max": 0.31} nginx cpu-ms/req 1.126
redis: ops/s 755.6 ops/req 9.445 cpu cores 0.006 clients 255 mem 888.8MB ping(us) {'n': 28228, 'p50_us': 508.2, 'p90_us': 595.2, 'p99_us': 690.4, 'p99.9_us': 2382.6, 'max_us': 4957.4, 'mean_us': 528.4}
redis cmdstats: {'info': {'calls_per_s': 0.0, 'usec_per_call': 141.0}, 'ping': {'calls_per_s': 93.9, 'usec_per_call': 0.11}, 'evalsha': {'calls_per_s': 0.4, 'usec_per_call': 12.01}, 'xadd': {'calls_per_s': 158.3, 'usec_per_call': 3.68}, 'mget': {'calls_per_s': 143.5, 'usec_per_call': 0.38}, 'hgetall': {'calls_per_s': 358.8, 'usec_per_call': 0.2}, 'get': {'calls_per_s': 0.4, 'usec_per_call': 0.7}, 'decrby': {'calls_per_s': 0.4, 'usec_per_call': 0.48}}
wire: {'requests_in_window': 24000, 'unit_ip_bytes_per_req': {'unit_to_client_side': 56075.1, 'client_side_to_unit': 8200.0, 'unit_to_provider': 8966.3, 'provider_to_unit': 56756.1, 'unit_to_redis': 5581.8, 'redis_to_unit': 380.2}, 'edge_ip_bytes_per_req': {'edge_to_clients': 56195.9, 'clients_to_edge': 8377.4}, 'olg_resp_body_bytes_mean': {'sse': 67313.6, 'json': 1433.8}}
