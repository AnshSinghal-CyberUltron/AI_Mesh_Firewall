| Pattern (translated, 32 KiB, timeout=5) | prose: stock / control / patched ms | code: stock / control / patched ms | patched / control (worse of the two texts) |
|---|---|---|---|
| `\bpassword\b` | 0.02 / 0.02 / 0.02 | 0.02 / 0.02 / 0.02 | 0.95x |
| `(?i)\bconfidential\b` | 0.11 / 0.11 / 0.11 | 0.12 / 0.12 / 0.12 | 1.02x |
| `\w+@\w+\.\w+` | 0.02 / 0.02 / 0.01 | 0.02 / 0.02 / 0.01 | 0.54x |
| `\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}` | 0.28 / 0.31 / 0.29 | 0.50 / 0.54 / 0.55 | 1.02x |
| `api[_-]?key\s*[:=]\s*\w{16,}` | 0.03 / 0.03 / 0.03 | 0.03 / 0.03 / 0.02 | 0.94x |
| `(?i)bitcoin` | 0.10 / 0.10 / 0.10 | 0.29 / 0.32 / 0.34 | 1.06x |
| `AKIA[0-9A-Z]{16}` | 0.02 / 0.02 / 0.01 | 0.02 / 0.03 / 0.02 | 0.66x |
| `\bEMP-\d{6}\b` | 0.02 / 0.02 / 0.01 | 0.02 / 0.03 / 0.02 | 0.65x |
| `(?i)\bproject\s+falcon\b` | 0.02 / 0.02 / 0.02 | 0.02 / 0.02 / 0.02 | 1.07x |
| `\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b` | 0.03 / 0.03 / 0.03 | 0.22 / 0.24 / 0.24 | 1.07x |
| `\b\w+\b` | 4.51 / 4.17 / 4.23 | 3.33 / 2.90 / 2.83 | 1.01x |
| `\b\w+\s+(?:token|secret)\b` | 4.82 / 4.49 / 4.17 | 5.68 / 3.92 / 3.65 | 0.93x |
| `(?i)\binternal use only\b` | 0.02 / 0.02 / 0.02 | 0.01 / 0.01 / 0.01 | 0.76x |
| `\b\d{3}-\d{2}-\d{4}\b` | 0.04 / 0.02 / 0.01 | 0.04 / 0.02 / 0.01 | 0.54x |
| `(?i)\bproject\s+\w+\b` | 0.03 / 0.02 / 0.02 | 0.02 / 0.02 / 0.02 | 0.91x |
Sum over 30 pattern-text pairs: stock 20.41 ms, control 17.65 ms, patched 16.95 ms (patched/control 0.960).

| Check-heavy cubic search, 260 digits (best of 7 x 3 rounds) | stock ms | control ms | patched ms |
|---|---|---|---|
| timeout=None | 52.10 | 54.77 | 54.18 |
| timeout=1000 | 52.83 | 54.95 | 55.79 |
