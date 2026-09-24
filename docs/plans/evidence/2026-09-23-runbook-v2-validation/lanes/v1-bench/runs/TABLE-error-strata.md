| run | measured | ok (200 complete) | policy blocks (benign FP) | infra errors | infra error classes | v1 audit dispositions |
|---|---|---|---|---|---|---|
| L1-r1 | 300 | 300 | 0 | 0 (0.00%) |  | {'ALLOW': 375} |
| L1b-r1 | 300 | 300 | 0 | 0 (0.00%) |  | {'ALLOW': 375} |
| L1c-r1 | 300 | 300 | 0 | 0 (0.00%) |  | {'ALLOW': 375} |
| L1-r2 | 600 | 600 | 0 | 0 (0.00%) |  | {'ALLOW': 750} |
| L1-r5 | 1500 | 1500 | 0 | 0 (0.00%) |  | {'ALLOW': 1875} |
| L1-r10 | 3000 | 3000 | 0 | 0 (0.00%) |  | {'ALLOW': 3750} |
| L1-r15 | 4500 | 4500 | 0 | 0 (0.00%) |  | {'ALLOW': 5625} |
| L1-r20 | 6000 | 6000 | 0 | 0 (0.00%) |  | {'ALLOW': 7500} |
| L1-r25 | 7500 | 7500 | 0 | 0 (0.00%) |  | {'ALLOW': 9375} |
| L1b-r25 | 7500 | 7500 | 0 | 0 (0.00%) |  | {'ALLOW': 9375} |
| L1c-r25 | 7500 | 7500 | 0 | 0 (0.00%) |  | {'ALLOW': 9375} |
| L1-r30 | 9000 | 6047 | 0 | 2953 (32.81%) | timeout 2953 | {'ALLOW': 9015} |
| SAT-r40 | 12000 | 5523 | 0 | 6477 (53.98%) | timeout 6475, http_503:kill_switch_active 2 | {'ALLOW': 9513} |
| N-r1 | 300 | 300 | 0 | 0 (0.00%) |  | {'ALLOW': 375} |
| N-r10 | 3000 | 3000 | 0 | 0 (0.00%) |  | {'ALLOW': 3750} |
| N-r25 | 7500 | 7500 | 0 | 0 (0.00%) |  | {'ALLOW': 9375} |
| N-r50 | 15000 | 5704 | 0 | 9296 (61.97%) | timeout 9149, http_503:circuit_breaker_open 74, http_422:no_provider_configured 58, http_503:kill_switch_active 15 | {'ALLOW': 10594} |
| OVL-r100 | 12000 | 3447 | 0 | 8553 (71.28%) | timeout 5202, http_422:no_provider_configured 1845, http_503:circuit_breaker_open 1413, http_503:kill_switch_active 93 | {'ALLOW': 4462} |
| ITL10-sse-r10 | 1800 | 1800 | 0 | 0 (0.00%) |  | {'ALLOW': 2150} |
| ITL20-sse-r10 | 1800 | 1800 | 0 | 0 (0.00%) |  | {'ALLOW': 2150} |
| ITL30-sse-r10 | 1800 | 1800 | 0 | 0 (0.00%) |  | {'ALLOW': 2150} |
| JSON-r10 | 1800 | 1800 | 0 | 0 (0.00%) |  | {'ALLOW': 2150} |
| GO-r10 | 3000 | 3000 | 0 | 0 (0.00%) |  | {'ALLOW': 3750} |
| FWOFF-r10 | 3000 | 3000 | 0 | 0 (0.00%) |  | {'ALLOW': 2625} |
