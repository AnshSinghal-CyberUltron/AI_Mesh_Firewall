#!/usr/bin/env bash
# Build static linux/amd64 binaries into bin/ with the harness git SHA embedded, and record checksums.
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.local/go/bin:$PATH"
sha=$(git rev-parse --short=12 HEAD 2>/dev/null || echo nogit)
if ! git diff --quiet HEAD -- . 2>/dev/null; then sha="${sha}-dirty"; fi
for cmd in synthprov olg rvproxy; do
  CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath \
    -ldflags "-s -w -X rvharness/internal/rv.BuildSHA=${sha}" -o "bin/${cmd}" "./cmd/${cmd}"
done
(cd bin && sha256sum synthprov olg rvproxy > SHA256SUMS)
echo "built ${sha}"; cat bin/SHA256SUMS
for b in synthprov olg rvproxy; do if readelf -d "bin/$b" 2>&1 | grep -q "no dynamic section"; then echo "static: $b"; else echo "NOT STATIC: $b"; exit 1; fi; done
