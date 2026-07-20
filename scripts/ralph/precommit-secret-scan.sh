#!/usr/bin/env bash
# Ralph chat-stress session — pre-commit secret-scan guard.
#
# Purpose
#   Prevent any provider secret — especially the runtime OpenRouter key used for
#   live client validation (R5) — from ever entering a commit.
#
# Safety
#   This script stores NO secret value. It carries only:
#     * a one-way SHA-256 fingerprint of the runtime key (not reversible), and
#     * structural regexes.
#   Token prefixes are assembled at runtime from fragments so this file's own
#   source never contains a literal provider-key prefix (no self-trigger).
#
# Install (Ralph also drops a direct copy at .git/hooks/pre-commit):
#   cp scripts/ralph/precommit-secret-scan.sh .git/hooks/pre-commit
#   chmod +x .git/hooks/pre-commit
#
# Behaviour
#   (1) EXACT fingerprint match of the live runtime key — checked in EVERY staged
#       file, no exclusions. This is the anti-leak core.
#   (2) Generic real-shape OpenRouter/Anthropic token in a NON-fixture file.
#       Redaction-test fixtures legitimately embed FAKE keys (they are exactly
#       what the firewall must detect/redact) and are allowlisted so this guard
#       never blocks the golden-suite / detector work.
set -uo pipefail

# --- one-way fingerprint of the live runtime key (safe to commit; NOT reversible)
LIVE_KEY_SHA256="93a9a831c12f3119e2fc91ec770d58371ae77953695d6c385ee562f90e3f04f2"

# --- assemble structural needles at runtime (keeps this file's source clean)
OR="sk-""or-v1-"          # OpenRouter runtime keys
ANT="sk-""ant-api"        # Anthropic keys

fail=0
note() { printf 'SECRET-SCAN: %s\n' "$1" >&2; fail=1; }

# All added content of the staged diff (new lines being committed).
added="$(git diff --cached --no-color -U0 | grep -E '^\+' | grep -vE '^\+\+\+' || true)"
if [ -z "$added" ]; then
  exit 0
fi

# (1) EXACT live-key fingerprint match — applies to EVERY file, no exclusions.
while IFS= read -r tok; do
  [ -n "$tok" ] || continue
  h="$(printf '%s' "$tok" | sha256sum | cut -d' ' -f1)"
  if [ "$h" = "$LIVE_KEY_SHA256" ]; then
    note "the live runtime OpenRouter key appears in the staged diff (fingerprint match)."
  fi
done < <(printf '%s\n' "$added" | grep -oE "${OR}[A-Za-z0-9]{40,}" || true)

# (2) Generic real-shape token in a NON-fixture file (per-file so we can exclude
#     vetted redaction-test fixtures precisely).
fixture_re='(tests?/|/leakhunt/|patterns\.py$|corpus\.py$|precommit-secret-scan\.sh$|ATTACK_LANDSCAPE\.md$|SOLUTIONS\.md$)'
while IFS= read -r f; do
  [ -n "$f" ] || continue
  if printf '%s' "$f" | grep -Eq "$fixture_re"; then
    continue
  fi
  fadd="$(git diff --cached --no-color -U0 -- "$f" | grep -E '^\+' | grep -vE '^\+\+\+' || true)"
  if printf '%s' "$fadd" | grep -Eq "${OR}[A-Za-z0-9]{40,}"; then
    note "a real-shape OpenRouter token appears in non-fixture file: $f"
  fi
  if printf '%s' "$fadd" | grep -Eq "${ANT}[A-Za-z0-9_-]{80,}"; then
    note "a real-shape Anthropic token appears in non-fixture file: $f"
  fi
done < <(git diff --cached --name-only --diff-filter=ACM || true)

if [ "$fail" -ne 0 ]; then
  printf '\n%s\n' "COMMIT BLOCKED: remove the secret(s) above and re-stage. (Ralph secret-scan guard)" >&2
  exit 1
fi
exit 0
