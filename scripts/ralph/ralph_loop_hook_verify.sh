#!/usr/bin/env bash
# Verify Ralph loop hook wiring (project .cursor/hooks.json + scripts).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "OK: $*"; }

[[ -f .cursor/hooks.json ]] || fail "missing .cursor/hooks.json"
[[ -x .cursor/ralph/hooks/stop-hook.sh ]] || fail "stop-hook.sh missing or not executable"
[[ -x .cursor/ralph/hooks/capture-response.sh ]] || fail "capture-response.sh missing or not executable"
[[ -f .cursor/ralph/scratchpad.md ]] || fail "no active loop — .cursor/ralph/scratchpad.md missing"

command -v jq >/dev/null 2>&1 || fail "jq not installed (required by hooks)"
command -v perl >/dev/null 2>&1 || fail "perl not installed (required by capture-response)"

ITER_BEFORE=$(sed -n '/^---$/,/^---$/{ /^---$/d; p; }' .cursor/ralph/scratchpad.md | awk -F': ' '/^iteration:/{print $2}')
[[ "$ITER_BEFORE" =~ ^[0-9]+$ ]] || fail "invalid iteration in scratchpad: $ITER_BEFORE"

OUT=$(CURSOR_PROJECT_DIR="$ROOT" echo '{"status":"completed","loop_count":0}' | .cursor/ralph/hooks/stop-hook.sh)
echo "$OUT" | jq -e '.followup_message | length > 0' >/dev/null || fail "stop-hook did not emit followup_message"

ITER_AFTER=$(sed -n '/^---$/,/^---$/{ /^---$/d; p; }' .cursor/ralph/scratchpad.md | awk -F': ' '/^iteration:/{print $2}')
EXPECTED=$((ITER_BEFORE + 1))
[[ "$ITER_AFTER" == "$EXPECTED" ]] || fail "iteration not incremented ($ITER_BEFORE -> $ITER_AFTER, expected $EXPECTED)"

# Restore iteration (verify is non-destructive to loop counter)
TEMP="${ROOT}/.cursor/ralph/scratchpad.md.tmp.$$"
sed "s/^iteration: .*/iteration: $ITER_BEFORE/" .cursor/ralph/scratchpad.md > "$TEMP"
mv "$TEMP" .cursor/ralph/scratchpad.md

ok "hooks.json present; stop-hook emits followup_message; iteration bump reversible"
echo "NEXT: Cursor Settings → Hooks — confirm afterAgentResponse + stop are enabled for this workspace."
echo "      Reload window (Cmd/Ctrl+Shift+P → Developer: Reload Window) if hooks were just added."
