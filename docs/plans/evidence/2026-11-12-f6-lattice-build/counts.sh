#!/usr/bin/env bash
# F6 Lattice build plan: reproducible counts over frontend/src at the checked-out commit.
# Usage: bash counts.sh <repo-root>   (writes to stdout; counts.txt holds the run of 12 Nov)
set -euo pipefail
ROOT="${1:-.}"
SRC="$ROOT/frontend/src"
PALETTE='\b(bg|text|border|ring|ring-offset|from|to|via|fill|stroke|divide|outline|placeholder|shadow|decoration|accent|caret|border-[trblxy])-(slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-[0-9]{2,3}\b'

echo "commit: $(git -C "$ROOT" rev-parse HEAD)"
echo "A-05 raw Tailwind palette classes (.js/.jsx): $(grep -rEoh --include='*.jsx' --include='*.js' "$PALETTE" "$SRC" | wc -l)"
echo "A-05 files containing them: $(grep -rElc --include='*.jsx' --include='*.js' "$PALETTE" "$SRC" | wc -l)"
echo "A-05 top 10 files (occurrences, not lines):"
grep -rEo --include='*.jsx' --include='*.js' "$PALETTE" "$SRC" | cut -d: -f1 | sort | uniq -c | sort -nr | head -10 | sed 's/^/  /'
echo "dark: variant usages: $(grep -rEoh --include='*.jsx' --include='*.js' '\bdark:[a-z]' "$SRC" | wc -l)"
echo "hex colour literals in .js/.jsx: $(grep -rEoh --include='*.jsx' --include='*.js' '#[0-9a-fA-F]{6}\b' "$SRC" | wc -l)"
echo "Google Fonts @imports in index.css: $(grep -c 'fonts.googleapis' "$SRC/index.css")"
echo "role=tablist / Tabs / SegmentedControl usages outside ui/:"
grep -rn 'role="tablist"\|<Tabs\b\|<TabsList\|SegmentedControl' --include='*.jsx' "$SRC" \
  | grep -v 'ui/Tabs.jsx\|ui/SegmentedControl.jsx' | awk -F: '{print $1}' | sort | uniq -c | sort -nr | sed 's/^/  /'
echo "frontend source lines (.js/.jsx): $(find "$SRC" -name '*.js' -o -name '*.jsx' | xargs cat | wc -l)"
echo "distinct /api/ paths referenced (approx.): $(grep -rhoE "/api/[a-z0-9_./-]+" --include='*.js' --include='*.jsx' "$SRC" | sed 's#[0-9]\+/#{id}/#g' | sort -u | wc -l)"
