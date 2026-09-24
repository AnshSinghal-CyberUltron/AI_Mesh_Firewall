select metadata->>'event_type' et, action, count(*) from policy_enforcementevent where created_at >= '2026-09-23T06:32:23Z' group by 1,2 order by 1,2;
select id, action, metadata->>'event_type' et, metadata->>'request_id' rid, left(metadata->'extra'->>'input_text', 80) it, left(metadata->>'prompt_submitted', 80) ps,
  (select string_agg((s->>'name') || ':' || coalesce(s->>'action',''), ',') from jsonb_array_elements(metadata->'extra'->'pipeline_trace'->'stages') s) stages
from policy_enforcementevent where created_at >= '2026-09-23T06:32:23Z' and metadata->>'event_type'='stream_complete' limit 2;
select jsonb_object_keys(metadata->'extra') from policy_enforcementevent where created_at >= '2026-09-23T06:32:23Z' and metadata->>'event_type'='stream_complete' limit 30;
