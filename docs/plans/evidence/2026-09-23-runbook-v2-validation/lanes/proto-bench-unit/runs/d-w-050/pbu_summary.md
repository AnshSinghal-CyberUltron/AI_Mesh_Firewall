# d-w-050: strict PASS | load-knee PASS (direct)

checks: {'zero_schedule_drops': True, 'zero_errors': True, 'run_valid': True, 'loadgen_cpu_lt_70': True}
load-knee checks: {'p99_T_fw_addon_nohold_lt_slo': True, 'infra_error_rate_le_budget': True, 'zero_schedule_drops': True, 'zero_safety_failures': True, 'run_valid': True}
offered 15000 (50.0/s) qualified 15000 (50.0/s) FP-blocks 0 (0.0) expected-blocks 0 infra 0 (0.0) drops 0 safety 0 detection-misses 0
by class: {'benign': {'qualified': 15000}}
infra reasons: {}
infra detail: {}

T_fw_addon: n=15000 p50=0.2636 p90=0.5271 p99=0.7801 p99.9=1.0827 max=9.1905 mean=0.3103
T_fw_addon_nohold: n=15000 p50=0.2159 p90=0.4233 p99=0.6676 p99.9=0.8009 max=9.1873 mean=0.2528
T_fw_addon_sse: n=10500 p50=0.2727 p90=0.5251 p99=0.7961 p99.9=1.1225 max=9.1905 mean=0.31
T_fw_addon_json: n=4500 p50=0.2483 p90=0.5286 p99=0.7303 p99.9=0.8347 max=9.1873 mean=0.3111
T_addon_first_sse: n=10500 p50=0.2124 p90=0.4102 p99=0.6212 p99.9=0.7884 max=1.0987 mean=0.2489
T_addon_total_sse: n=10500 p50=0.1912 p90=0.3801 p99=0.5544 p99.9=0.738 max=0.8782 mean=0.2278
T_addon_total_json: n=4500 p50=0.2483 p90=0.5286 p99=0.7303 p99.9=0.8347 max=9.1873 mean=0.3111
T_release_lag_max: n=1043 p50=0.6077 p90=0.7915 p99=1.1225 p99.9=5.4125 max=9.1905 mean=0.5965
client_ttft_sse: n=10500 p50=150.3651 p90=150.5988 p99=150.8434 p99.9=150.9979 max=151.2843 mean=150.3946
lateness: n=15000 p50=0.0847 p90=0.0977 p99=0.1352 p99.9=0.1786 max=0.2685 mean=0.0849
loadgen: [{'vm': 'rv-pbu-lg-1', 'busy_max': 15.3, 'busy_mean': 4.4, 'late_max_us': 455, 'conn_opens': 284, 'max_inflight': 204}, {'vm': 'rv-pbu-lg-2', 'busy_max': 17.0, 'busy_mean': 4.1, 'late_max_us': 268, 'conn_opens': 281, 'max_inflight': 204}]
wire per client request: {'client_to_gw': 16768.1, 'gw_to_client': 102079.0, 'gw_to_provider': 16792.1, 'provider_to_gw': 102055.2}
