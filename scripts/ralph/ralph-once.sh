#!/usr/bin/env bash
# Single Ralph iteration (debug one story). Fresh Claude Code instance, CLAUDE.md as prompt.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
claude --dangerously-skip-permissions --print < "$SCRIPT_DIR/CLAUDE.md"
