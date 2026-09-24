#!/usr/bin/env bash
# Tasks 2-4: print every line range cited in the final report, so each file:line
# claim has a saved command+output. Read-only (sed/grep on source files).
R=/home/contact_cyberultron_com/AI_Mesh_Firewall
C=$R/control/ai_mesh_control
G=$R/gateway/ai_mesh_gateway
show() { echo; echo "##### $1:$2-$3"; sed -n "$2,$3p" "$1" | nl -ba -v "$2"; }

echo "================ FIREWALLCONFIG ================"
show $C/main_app/urls.py 34 34
show $C/core/models.py 1370 1388
show $C/core/signals.py 404 440
show $C/core/signals.py 444 455
show $C/core/signals.py 229 247
show $C/main_app/settings.py 493 498
show $G/config_sync.py 24 28
show $G/config_sync.py 170 181
show $G/config_sync.py 478 501
show $G/config_sync.py 547 558
show $G/config_sync.py 596 626
show $G/config_sync.py 649 690

echo "================ LLM MODEL CONFIG (/api/firewall/models/) ================"
show $C/core/llm_model_urls.py 8 9
show $C/core/signals.py 481 505
show $C/core/signals.py 546 575
show $G/config_sync.py 503 525
show $G/config_sync.py 701 719

echo "================ POLICIES (/api/policies/*) ================"
show $C/policy/urls.py 10 21
show $C/policy/compiler_signals.py 49 70
show $C/policy/compiler_signals.py 92 100
show $C/policy/compiler_signals.py 166 190
show $C/policy/views.py 1014 1046
show $C/policy/compile_views.py 106 116
show $C/policy/compiler.py 32 34
show $C/policy/compiler.py 141 146
show $C/policy/compiler.py 215 223
show $C/policy/compiler.py 250 253
show $C/policy/compiler.py 320 347
show $C/policy/signing.py 14 23
show $C/policy/signing.py 40 43
show $G/policy_sync.py 35 37
show $G/policy_sync.py 105 118
show $G/policy_sync.py 150 166
show $G/policy_sync.py 305 312
show $G/policy_sync.py 438 441
show $G/policy_sync.py 509 520

echo "================ VECTOR POLICIES ================"
show $C/policy/vector_urls.py 9 12
show $C/policy/vector_views.py 158 178
show $C/policy/vector_views.py 299 305
show $C/policy/vector_signals.py 85 101
show $C/policy/vector_compiler.py 26 28
show $C/policy/vector_compiler.py 108 115
show $C/policy/vector_compiler.py 164 184
show $G/vector_policy_sync.py 20 22
show $G/vector_policy_sync.py 167 176
show $G/vector_policy_sync.py 207 210
show $G/vector_policy_sync.py 225 236
show $G/vector_policy_sync.py 270 282
show $G/main.py 6615 6621
show $G/config.py 175 175

echo "================ VECTOR PROVIDERS ================"
show $C/policy/vector_provider_urls.py 9 9
show $C/policy/vector_provider_signals.py 24 35
show $C/policy/vector_provider_signals.py 57 82
show $C/policy/vector_provider_signals.py 95 101
show $G/vector_provider_sync.py 29 37
show $G/vector_provider_sync.py 110 136
show $G/vector_provider_sync.py 152 155
show $G/main.py 6653 6654

echo "================ KILL SWITCHES ================"
show $C/core/kill_switch_urls.py 8 8
show $C/core/kill_switch_views.py 191 206
show $C/core/kill_switch_views.py 241 249
show $C/core/models.py 837 867
show $C/core/signals.py 279 345
show $C/core/admin_urls.py 65 69
show $C/core/redis_kill_switch_views.py 31 38
show $C/core/redis_kill_switch_views.py 116 140
show $G/kill_switch.py 44 65
show $G/circuit_breaker.py 138 141
show $G/circuit_breaker.py 205 212
show $G/circuit_breaker.py 234 253

echo "================ GATEWAY API KEYS ================"
show $C/core/gateway_urls.py 20 38
show $C/core/signals.py 45 45
show $C/core/signals.py 72 115
show $C/core/signals.py 124 130
show $C/core/signals.py 253 276
show $C/core/models.py 735 751
show $C/core/gateway_key_views.py 377 384
show $C/core/gateway_key_views.py 428 444
show $C/core/gateway_views.py 243 262
show $C/core/gateway_views.py 350 362
show $C/core/gateway_views.py 389 410
show $C/core/simulator_seed.py 105 133
show $C/core/gateway_instance_views.py 84 99
show $C/main_app/settings.py 487 492
show $G/middleware.py 30 30
show $G/middleware.py 201 205
show $G/vector_routes.py 353 356

echo "================ MODEL STATE (/api/models/*) ================"
show $C/core/model_state_urls.py 15 21
show $C/core/model_state_views.py 128 150
show $C/core/model_state_views.py 242 301
show $C/core/model_state_views.py 338 366
show $C/core/models.py 1877 1895
show $C/core/signals.py 351 398
show $G/model_state.py 52 57
show $G/model_state.py 86 91
show $G/model_state.py 245 250
show $G/model_state.py 278 281
show $G/model_state.py 301 306
show $G/model_state.py 330 333
show $G/main.py 10553 10556
show $G/main.py 12483 12488

echo "================ CIRCUIT BREAKER (/api/admin/gateway/circuit-breaker/*) ================"
show $C/core/admin_urls.py 24 42
show $C/core/gateway_admin_proxy_views.py 64 90
show $C/core/gateway_admin_proxy_views.py 137 163
show $G/main.py 15992 16000
show $G/main.py 16012 16020
show $G/main.py 16026 16060

echo "================ MCP CONNECTOR (/api/mcp-connector/*) ================"
show $C/mcp_connector/urls.py 9 35
show $C/mcp_connector/signals.py 40 40
show $C/mcp_connector/signals.py 79 104
show $C/mcp_connector/signals.py 120 124
show $C/mcp_connector/signals.py 152 156
show $C/mcp_connector/signals.py 202 216
show $C/mcp_connector/views.py 367 420
show $C/mcp_connector/views.py 738 740
show $C/mcp_connector/views.py 798 806
show $C/mcp_connector/views.py 2535 2537
show $C/mcp_connector/views.py 2608 2652
show $C/mcp_connector/views.py 2800 2870
show $C/mcp_connector/views.py 3039 3045
show $C/mcp_connector/views.py 3120 3150
show $G/mcp_proxy.py 64 66
show $G/mcp_proxy.py 77 90
show $G/mcp_proxy.py 650 650
show $G/mcp_proxy.py 670 690
show $G/mcp_proxy.py 731 760
show $G/mcp_proxy.py 883 930
show $G/mcp_proxy.py 945 980
show $G/mcp_proxy.py 1250 1263
show $G/mcp_proxy.py 3665 3715
show $G/mcp_oauth_proxy.py 55 60
show $G/mcp_oauth_proxy.py 103 108

echo "================ /api/mcp/* (legacy MCPManagerPanel) ================"
echo "--- Django route includes for api/mcp (expect only mcp-connector):"
grep -rn --include=*.py 'path("api/mcp' $C | grep -v /tests/
show $R/deploy/nginx.conf 88 93
echo "--- frontend callers:"
grep -n '/api/mcp/' $R/frontend/src/components/MCPManagerPanel.jsx
echo "--- importers of MCPManagerPanel / MCPScannerPanel (non-comment):"
grep -rn "import.*MCPManagerPanel\|import.*MCPScannerPanel\|<MCPManagerPanel\|<MCPScannerPanel" $R/frontend/src --include=*.jsx --include=*.js --include=*.tsx --include=*.ts | grep -v '.claude-flow' || echo "(none)"
echo "--- any server implementing /api/mcp/servers|status|policies routes (py/js/ts outside frontend):"
grep -rln 'api/mcp/status\|"/api/mcp/servers\|api/mcp/policies' --include=*.py --include=*.go --include=*.ts --include=*.js $R/gateway $R/control $R/services $R/shared 2>/dev/null || echo "(none)"

echo "================ REVERSE-DIRECTION / NON-CONFIG SHARED KEYS ================"
show $C/core/ingestion_views.py 205 209
show $G/main.py 16836 16838
show $G/mcp_error_classifier.py 213 224
show $C/mcp_connector/views.py 17 33
show $G/jobs.py 54 56
show $C/core/tasks.py 944 948
show $C/main_app/settings.py 1005 1010
show $G/log_buffer.py 140 150
show $G/log_buffer.py 178 181

echo "================ VERSION FIELD CHECKS ================"
echo "--- gateway non-test reads of bundle_schema_version / schema_version / config_version:"
grep -rn --include=*.py "bundle_schema_version\|schema_version\|config_version" $G | grep -v /tests/ || echo "(none)"
echo "--- gateway reads of policies:version / vector:policies:version constants (beyond definition):"
grep -n "REDIS_KEY_VERSION" $G/policy_sync.py $G/vector_policy_sync.py
echo "--- version/signature tokens in FirewallConfig / LLM / apikey / killswitch / modelstate payload builders:"
sed -n 1375,1471p $C/core/models.py | grep -n "version\|_sig\|schema" || echo "(firewall payload: none)"
sed -n 481,500p $C/core/signals.py | grep -n "version\|_sig\|schema" || echo "(llm payload: none)"
sed -n 735,751p $C/core/models.py | grep -n "version\|_sig\|schema" || echo "(apikey payload: none)"
sed -n 846,867p $C/core/models.py | grep -n "version\|_sig\|schema" || echo "(killswitch payload: none)"
sed -n 1881,1895p $C/core/models.py | grep -n "version\|_sig\|schema" || echo "(modelstate payload: none)"
