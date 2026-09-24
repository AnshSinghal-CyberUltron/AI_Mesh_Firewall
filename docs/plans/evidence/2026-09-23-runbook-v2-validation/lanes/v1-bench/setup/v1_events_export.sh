#!/usr/bin/env bash
# v1_events_export.sh <since_utc_iso> <out.jsonl>   (runs ON the SUT)
# Exports v1's own per-request audit events (policy_enforcementevent, event_type request (JSON) or
# stream_complete (SSE)) created
# since the given time: action, nonce-bearing input prefix, per-stage name:action:latency, and the
# trace's addon split. One JSON object per line.
set -euo pipefail
since=$1; out=$2
cat > /tmp/v1exp.sql <<SQL
\copy (select json_build_object('id', id, 'ts', extract(epoch from created_at), 'action', action, 'org', organization_id, 'rid', metadata->>'request_id', 'status_code', metadata->>'status_code', 'et', metadata->>'event_type', 'inp', left(coalesce(metadata->'extra'->>'input_text', metadata->>'prompt_submitted', metadata->'extra'->>'prompt_snippet'), 400), 'completed', metadata->'extra'->>'completed', 'had_error', metadata->'extra'->>'had_error', 'output_blocked', metadata->'extra'->>'output_blocked', 'client_corr', metadata->'extra'->>'client_correlation_id', 'stages', (select string_agg((s->>'name') || ':' || coalesce(s->>'action','') || ':' || coalesce(s->>'latency_ms',''), ',') from jsonb_array_elements(metadata->'extra'->'pipeline_trace'->'stages') s), 'total_ms', metadata->'extra'->'pipeline_trace'->>'total_latency_ms', 'pre_ms', metadata->'extra'->'pipeline_trace'->>'t_addon_pre_ms', 'post_ms', metadata->'extra'->'pipeline_trace'->>'t_addon_post_ms', 'overhead_ms', metadata->'extra'->'pipeline_trace'->>'overhead_ms', 'ttft_ms', metadata->'extra'->'pipeline_trace'->>'ttft_ms', 'final', metadata->'extra'->'pipeline_trace'->>'final_action') from policy_enforcementevent where metadata->>'event_type' in ('request', 'stream_complete') and created_at >= '$since') to stdout
SQL
sudo docker cp /tmp/v1exp.sql aimeshperf-postgres-1:/tmp/v1exp.sql
sudo docker exec aimeshperf-postgres-1 psql -U ai_mesh_firewall -d ai_mesh_firewall -At -f /tmp/v1exp.sql > "$out"
wc -l < "$out"
