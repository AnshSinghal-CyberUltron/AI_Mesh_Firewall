select id, action, event_class, metadata->>'request_id' rid, metadata->>'pipeline_stage' ps, metadata->>'event_type' et,
       left(metadata->'extra'->>'input_text', 90) it,
       (select string_agg((s->>'name') || ':' || (s->>'action'), ',') from jsonb_array_elements(metadata->'extra'->'pipeline_trace'->'stages') s) stages
from policy_enforcementevent order by id desc limit 6;
select jsonb_object_keys(metadata->'extra') from policy_enforcementevent where id=(select max(id) from policy_enforcementevent);
