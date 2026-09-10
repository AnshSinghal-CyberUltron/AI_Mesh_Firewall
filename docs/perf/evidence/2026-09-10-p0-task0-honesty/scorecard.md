# P0.0 scorecard (SHA a67337fb / aimf_p0)

| Claim | Result |
|---|---|
| (a) Cell_A Firewall_Tax p99 vs 20 ms | FAIL (p50=25.3 p99=42.5 n=16) |
| (b) Cell_A Wall | N/A-not-tax (p50=2024.7 p99=2042.5) |
| (c) MASTER ≤12 ms latency (SLO F / T_addon_pre) | compare p50 tax 25.3 to 12 ms — **latency**, not CPU% |
| (d) stub ≠ capacity | PASS if capacity_eligible is false: False |
| (e) T2-on | N/A-measured (Cell_A ENABLE_TIER2=false) |
| (f) PG2 4.1/5.9 ms | N/A-off-SHA |
| (g) 100k RPS | N/A |

Cell_A stage p50/p99 ms: policy=4.7/5.1 input_scan=9.0/26.6 output_guardrail=1.6/1.7
preflight=pass excluded_A={} cpu=0.06% container=aimf_p0-gateway-1
capacity_eligible must stay false on stub (row d).
