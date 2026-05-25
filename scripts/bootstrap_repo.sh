#!/usr/bin/env bash
# One-time: init standalone product from parent monorepo (manual phases — does not auto-copy all code)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PARENT="$(cd "$ROOT/.." && pwd)"

echo "AI Mesh Firewall root: $ROOT"
echo "Parent monorepo:      $PARENT"
echo ""
echo "This scaffold is ready. Next steps:"
echo "  1. cp .env.sample .env"
echo "  2. make up"
echo "  3. Follow docs/MIGRATION_FROM_AIGUARDX.md phase-by-phase"
echo ""
echo "Optional symlink for gradual migration (dev only):"
echo "  ln -sf $PARENT/gateway $ROOT/_import/gateway"
echo "  ln -sf $PARENT/backend $ROOT/_import/backend"
