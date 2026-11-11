## 1. Translation (cold caches), thread CPU ms
| Pattern | Length | ci items | Translate | Compile |
|---|---|---|---|---|
| (?i) + 64 distinct literals | 68 | 64 | 3.8 | 2.9 |
| (?i) + 64 distinct 2-char sets | 260 | 64 | 9.3 | 4.7 |
| (?i) + 65 distinct literals (over the cap) | 69 | — | refused: more than 64 distinct case-insensitive items | — |

A hostile 64-rule plan (each rule (?i) + 64 distinct 2-char sets), translate + compile, cold: 1392 ms CPU
Event-loop lag while a worker thread translates + compiles that plan: p99 4.4 ms, worst 16.3 ms (791 ticks)

## 2. Compile at the limits (expanded repetition <= 1000)
| Pattern | Expanded | Compile CPU ms | Traced peak KiB |
|---|---|---|---|
| `x{1000}` | 1000 | 0.2 | 245 |
| `(?:ab){500}` | 500 | 0.2 | 128 |
| `(?:x{10}){100}` | — | refused: total counted repetition over 1000 | — |
| `[a-z0-9]{1000}` | 1000 | 0.3 | 477 |
| `(?:\w{3}\s){250}` | 1000 | 0.7 | 929 |
| `(?i)(?:[a-z]{10}){100}` | — | refused: total counted repetition over 1000 | — |
| `x{100} x 10` | 1000 | 0.3 | 253 |
| `(?:a|b|c|d|e|f|g|h){1000}` | 1000 | 0.3 | 392 |
| `990-char literal alternation` | 0 | 4.0 | 221 |
| `(?:\b\w{3}\b ){250} (one-sided)` | 1000 | 0.8 | 1298 |
| `x{1000} x 140 (B, rev 4)` | — | refused: total counted repetition over 1000 | — |

## 3. Parse-only check (analysis + limits, no translation), thread CPU ms
- `(?i)[źň][Uù][ĭƿ][Ȥį][Ǣq][ƕÃ][țb][ĵɀ][ēƂ]...` (260 chars): 0.23 ms
- `x{10}x{10}x{10}x{10}x{10}x{10}x{10}x{10}...` (500 chars): 0.53 ms
- `(?:\b\w{3}\b ){250}...` (19 chars): 0.03 ms
- `((((((((((((((((((((((((((((((((((((((((...` (201 chars): 0.52 ms
- `w000|w001|w002|w003|w004|w005|w006|w007|...` (989 chars): 1.21 ms
- 64-rule hostile plan: 16.5 ms

## 4. Match speed against `re` (32 KiB), thread CPU ms, best of 5
| Pattern | prose: re | prose: translated | code: re | code: translated | worst ratio |
|---|---|---|---|---|---|
| `\bpassword\b` | 0.43 | 0.02 | 0.59 | 0.02 | 0.0x |
| `(?i)\bconfidential\b` | 0.46 | 0.11 | 0.55 | 0.11 | 0.2x |
| `\w+@\w+\.\w+` | 0.95 | 0.02 | 1.60 | 0.02 | 0.0x |
| `\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}` | 0.41 | 0.32 | 0.46 | 0.50 | 1.1x |
| `api[_-]?key\s*[:=]\s*\w{16,}` | 0.03 | 0.03 | 0.02 | 0.03 | 0.7x |
| `(?i)bitcoin` | 0.17 | 0.10 | 0.20 | 0.32 | 1.6x |
| `AKIA[0-9A-Z]{16}` | 0.01 | 0.02 | 0.02 | 0.03 | 0.5x |
| `\bEMP-\d{6}\b` | 0.43 | 0.02 | 0.51 | 0.02 | 0.1x |
| `(?i)\bproject\s+falcon\b` | 0.40 | 0.02 | 0.55 | 0.02 | 0.1x |
| `\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b` | 0.48 | 0.03 | 0.58 | 0.23 | 0.4x |
| `\b\w+\b` | 1.34 | 4.72 | 0.97 | 2.91 | 3.5x |
| `\b\w+\s+(?:token|secret)\b` | 1.16 | 4.34 | 1.15 | 3.87 | 3.7x |
| `(?i)\binternal use only\b` | 0.51 | 0.02 | 0.60 | 0.01 | 0.0x |
| `\b\d{3}-\d{2}-\d{4}\b` | 0.53 | 0.02 | 0.61 | 0.02 | 0.0x |
| `(?i)\bproject\s+\w+\b` | 0.44 | 0.02 | 0.50 | 0.02 | 0.1x |
Worst ratio (each side floored at 0.05 ms): 3.7x

## 5. Streaming per-delta cost (10 rules), thread CPU ms per delta
Texts (deterministic, seed 3): `punctuated prose` (a period or comma about every 8 words, a newline about every 60); `unpunctuated prose` (words and spaces only: no stopper ever appears, the worst case for stopper-mode rules); `code` (assignments, calls, hex, base64-like strings).

B's 10 rules: modes `\bpassword\b` reach, `(?i)\bconfidential\b` reach, `\w+@\w+\.\w+` stopper, `\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}` reach, `api[_-]?key\s*[:=]\s*\w{16,}` stopper, `(?i)bitcoin` reach, `AKIA[0-9A-Z]{16}` reach, `\bEMP-\d{6}\b` reach, `(?i)\bproject\s+falcon\b` stopper, `\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b` reach
| Text | Output so far | Revision 4: whole rescan, all 10 | Revision 5: all 10, incremental | of which reach/stopper rules |
|---|---|---|---|---|
| punctuated prose | 4 KiB | 0.10 | 0.058 | 0.042 |
| punctuated prose | 32 KiB | 0.71 | 0.037 | 0.037 |
| punctuated prose | 128 KiB | 2.50 | 0.042 | 0.041 |
| punctuated prose | 512 KiB | 11.10 | 0.041 | 0.040 |
| unpunctuated prose | 4 KiB | 0.10 | 0.043 | 0.042 |
| unpunctuated prose | 32 KiB | 0.73 | 0.071 | 0.071 |
| unpunctuated prose | 128 KiB | 2.79 | 0.180 | 0.178 |
| unpunctuated prose | 512 KiB | 11.33 | 0.572 | 0.561 |
| code | 4 KiB | 0.18 | 0.037 | 0.037 |
| code | 32 KiB | 1.29 | 0.040 | 0.039 |
| code | 128 KiB | 5.04 | 0.040 | 0.040 |
| code | 512 KiB | 20.16 | 0.037 | 0.036 |

A's 10 rules: modes `\bpassword\b` reach, `(?i)\bconfidential\b` reach, `\w+@\w+\.\w+` stopper, `\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}` reach, `api[_-]?key\s*[:=]\s*\w{16,}` stopper, `(?i)bitcoin` reach, `AKIA[0-9A-Z]{16}` reach, `(?i)\binternal use only\b` reach, `\b\d{3}-\d{2}-\d{4}\b` reach, `\b\w+\s+(?:token|secret)\b` stopper
| Text | Output so far | Revision 4: whole rescan, all 10 | Revision 5: all 10, incremental | of which reach/stopper rules |
|---|---|---|---|---|
| punctuated prose | 4 KiB | 0.60 | 0.035 | 0.035 |
| punctuated prose | 32 KiB | 4.83 | 0.107 | 0.183 |
| punctuated prose | 128 KiB | 37.49 | 0.058 | 0.040 |
| punctuated prose | 512 KiB | 88.28 | 0.042 | 0.042 |
| unpunctuated prose | 4 KiB | 0.71 | 0.728 | 0.658 |
| unpunctuated prose | 32 KiB | 6.40 | 4.295 | 4.334 |
| unpunctuated prose | 128 KiB | 21.01 | 18.686 | 17.840 |
| unpunctuated prose | 512 KiB | 85.28 | 78.738 | 74.812 |
| code | 4 KiB | 0.71 | 0.041 | 0.041 |
| code | 32 KiB | 5.35 | 0.035 | 0.035 |
| code | 128 KiB | 19.51 | 0.039 | 0.039 |
| code | 512 KiB | 80.51 | 0.042 | 0.041 |
