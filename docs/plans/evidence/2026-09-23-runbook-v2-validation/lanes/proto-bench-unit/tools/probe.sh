#!/usr/bin/env bash
# probe.sh TARGET AUTH_FILE OUT_JSONL T0_MS T1_MS TAG
#   Between unix ms T0 and T1, send one small non-streaming chat request every 0.25 s and record
#   the HTTP status plus Retry-After / Retry-After-Ms headers (olg does not record response headers).
set -uo pipefail
target=$1 authf=$2 out=$3 t0=$4 t1=$5 tag=$6
auth=$(cat "$authf")
i=0
while [[ $(date +%s%3N) -lt $t0 ]]; do sleep 0.05; done
while [[ $(date +%s%3N) -lt $t1 ]]; do
  i=$((i + 1))
  body="{\"model\":\"gpt-4o-mini\",\"max_tokens\":5,\"messages\":[{\"role\":\"user\",\"content\":\"probe $tag $i: please say hello to the garden club\"}]}"
  ts=$(date +%s%3N)
  h=$(curl -s -m 30 -o /dev/null -D - -X POST "$target/v1/chat/completions" -H "Authorization: $auth" \
        -H 'Content-Type: application/json' -H "x-request-id: probe-$tag-$i" -d "$body" | tr -d '\r')
  te=$(date +%s%3N)
  st=$(awk 'NR==1{print $2}' <<<"$h")
  ra=$(awk -F': ' 'tolower($1)=="retry-after"{print $2}' <<<"$h")
  ram=$(awk -F': ' 'tolower($1)=="retry-after-ms"{print $2}' <<<"$h")
  printf '{"t_ms": %s, "rt_ms": %s, "i": %s, "status": "%s", "retry_after": "%s", "retry_after_ms": "%s"}\n' \
    "$ts" "$((te - ts))" "$i" "$st" "$ra" "$ram" >> "$out"
  sleep 0.25
done
