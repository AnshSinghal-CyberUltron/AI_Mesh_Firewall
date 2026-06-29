#!/usr/bin/env bash
# Pretty status of the Ralph backlog.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
jq -r '.userStories[] | "\(.passes|if . then "✅" else "⬜" end) [\(.priority)] \(.id) — \(.title)"' \
  "$SCRIPT_DIR/prd.json"
echo "---"
echo "remaining: $(jq -r '[.userStories[]|select(.passes==false)]|length' "$SCRIPT_DIR/prd.json") / $(jq -r '.userStories|length' "$SCRIPT_DIR/prd.json")"
