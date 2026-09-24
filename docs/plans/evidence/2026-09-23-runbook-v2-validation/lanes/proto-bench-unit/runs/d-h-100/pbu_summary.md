# d-h-100: strict PASS | load-knee PASS (direct)

checks: {'zero_schedule_drops': True, 'zero_errors': True, 'run_valid': True, 'loadgen_cpu_lt_70': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 30000 (100.0/s) qualified 30000 (100.0/s) FP-blocks 0 (0.0) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'qualified': 30000}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=30000 p50=0.2557 p90=0.4359 p99=0.6909 p99.9=1.1494 max=4.1686 mean=0.2816
T_fw_addon_nohold: n=30000 p50=0.222 p90=0.3533 p99=0.5943 p99.9=1.1274 max=4.1574 mean=0.2409
T_fw_addon_sse: n=21000 p50=0.2601 p90=0.4303 p99=0.6918 p99.9=1.1395 max=4.1686 mean=0.2826
T_fw_addon_json: n=9000 p50=0.2461 p90=0.4452 p99=0.6864 p99.9=1.1659 max=3.7763 mean=0.2793
T_addon_first_sse: n=21000 p50=0.2182 p90=0.3425 p99=0.5386 p99.9=1.0873 max=4.1686 mean=0.2349
T_addon_total_sse: n=21000 p50=0.2117 p90=0.3222 p99=0.4882 p99.9=1.0898 max=4.1574 mean=0.2244
T_addon_total_json: n=9000 p50=0.2461 p90=0.4452 p99=0.6864 p99.9=1.1659 max=3.7763 mean=0.2793
T_release_lag_max: n=2117 p50=0.4485 p90=0.6679 p99=0.8006 p99.9=1.0727 max=2.7426 mean=0.4616
client_ttft_sse: n=21000 p50=150.3337 p90=150.4566 p99=150.6905 p99.9=151.1676 max=154.2315 mean=150.3452
lateness: n=30000 p50=0.0807 p90=0.0917 p99=0.1062 p99.9=0.1876 max=0.3356 mean=0.0796
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 14.6, 'busy_mean': 3.6, 'late_max_us': 559, 'conn_opens': 328, 'max_inflight': 262}, {'vm': 'rv-pbu-lg-2', 'busy_max': 13.4, 'busy_mean': 3.2, 'late_max_us': 335, 'conn_opens': 314, 'max_inflight': 262}]
wire per client request: {'client_to_gw': 8708.2, 'gw_to_client': 58130.5, 'gw_to_provider': 8735.7, 'provider_to_gw': 58121.8}
