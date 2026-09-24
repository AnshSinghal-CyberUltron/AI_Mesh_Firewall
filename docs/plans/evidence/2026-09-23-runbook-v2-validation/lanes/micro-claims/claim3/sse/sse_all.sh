#!/bin/bash
D=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/micro-claims/claim3/sse
$D/sse_run.sh sse_direct direct http://10.160.0.57:8300
$D/sse_run.sh sse_A_gw A_prod http://aimeshgateway.zeroshield.ai
$D/sse_run.sh sse_A_fw_v1 A_prod http://aimeshfirewall.zeroshield.ai
$D/sse_run.sh sse_A_gw_gzip A_prod http://aimeshgateway.zeroshield.ai "-H Accept-Encoding:gzip,deflate"
$D/sse_run.sh sse_B_gw B_keepalive http://aimeshgateway.zeroshield.ai
$D/sse_run.sh sse_C_gw_buffer_on_NEGCTRL C_buffer_on http://aimeshgateway.zeroshield.ai
echo SSE ALL DONE $(date -u +%FT%TZ)
