# d-sse-040: strict PASS | load-knee PASS (direct)

checks: {'zero_schedule_drops': True, 'zero_errors': True, 'run_valid': True, 'loadgen_cpu_lt_70': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 12000 (40.0/s) qualified 12000 (40.0/s) FP-blocks 0 (0.0) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'qualified': 12000}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=12000 p50=0.2262 p90=0.2646 p99=0.5778 p99.9=1.5747 max=4.0357 mean=0.2423
T_fw_addon_nohold: n=12000 p50=0.1544 p90=0.1915 p99=0.3096 p99.9=1.5593 max=4.026 mean=0.163
T_fw_addon_sse: n=12000 p50=0.2262 p90=0.2646 p99=0.5778 p99.9=1.5747 max=4.0357 mean=0.2423
T_fw_addon_json: n=0
T_addon_first_sse: n=12000 p50=0.1628 p90=0.2028 p99=0.3216 p99.9=1.5643 max=4.0337 mean=0.1721
T_addon_total_sse: n=12000 p50=0.1544 p90=0.1915 p99=0.3096 p99.9=1.5593 max=4.026 mean=0.163
T_addon_total_json: n=0
T_release_lag_max: n=12000 p50=0.2262 p90=0.2646 p99=0.5778 p99.9=1.5747 max=4.0357 mean=0.2423
client_ttft_sse: n=12000 p50=150.2079 p90=150.249 p99=150.38 p99.9=151.6107 max=154.1183 mean=150.215
lateness: n=12000 p50=0.0877 p90=0.0999 p99=0.1169 p99.9=0.1504 max=0.3071 mean=0.0887
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 16.8, 'busy_mean': 4.5, 'late_max_us': 423, 'conn_opens': 152, 'max_inflight': 110}, {'vm': 'rv-pbu-lg-2', 'busy_max': 17.9, 'busy_mean': 4.0, 'late_max_us': 307, 'conn_opens': 149, 'max_inflight': 110}]
wire per client request: {'client_to_gw': 13575.2, 'gw_to_client': 82579.6, 'gw_to_provider': 13817.9, 'provider_to_gw': 82334.1}
