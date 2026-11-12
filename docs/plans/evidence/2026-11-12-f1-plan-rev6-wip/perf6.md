## 1. Reviewer B's revision-5 cases (`limits_attack.py`), revision 5 → revision 6

| Pattern | Rev. 5: passes limits? translated chars, compile ms, match 32 KiB ms | Rev. 6: verdict | Rev. 6 estimate (size / units) |
|---|---|---|---|
| `\s`×500 | passes; 39,000 chars, compile 54 ms, match 0.9 ms | refused: translated size 39000 over 16384 | 39,000 / 39,000 |
| `\b.`×330 | passes; 27,060 chars, compile 82 ms, match 13.0 ms | refused: translated size 27060 over 16384 | 27,060 / 27,060 |
| `(?=\w)`×166 + `a` | passes; 2,826 chars, compile 9 ms, match 17.9 ms | accepted | 2,826 / 2,826 |
| `(?<=\w)`×142 + `a` | passes; 2,560 chars, compile 5 ms, match 5.3 ms | accepted | 2,560 / 2,560 |
| 400-deep groups | **RecursionError** escapes | refused: groups nested deeper than 16 | — |
| 499-deep groups (projector poison) | **RecursionError** escapes | refused: groups nested deeper than 16 | — |
| 330-deep `(?:` | Refused: pattern longer than 1000 | refused: pattern longer than 1000 | — |
| `(a)+` nested 16 (new) | passes; 116 chars, compile 193 ms, match 415.6 ms | refused: compile units 1048564 over 32768 | 116 / 1,048,564 |
| `(?:a)+` nested 24 (new) | passes; compile not run (exponential: 16 levels 0.2-0.3 s, 32 levels exhausted memory) | refused: groups nested deeper than 16 | — |
| `(a)+` nested 32 (new: OOM-killed the probe) | passes; compile not run (exponential: 16 levels 0.2-0.3 s, 32 levels exhausted memory) | refused: groups nested deeper than 16 | — |

## 2. The worst accepted shape at each limit (revision 6)

Limits: length ≤ 1000, depth ≤ 16, translated size ≤ 16,384, compile units ≤ 32,768, ci items ≤ 64, expanded repetition ≤ 1000.

| Shape | Src chars | Translated (estimate) | Units (estimate) | Translate | Compile total | of which C (GIL held) | Retained / peak KiB | Match 32 KiB: prose / word / code | `re` prose / word / code |
|---|---|---|---|---|---|---|---|---|---|
| `\s` repeated (set expansion) | 420 | 16,380 (16,380) | 16,380 (16,380) | 0.7 | 31.9 | 0.23 | 165 / 612 | 1.2 / 0.9 / 1.7 | 0.3 / 0.2 / 0.3 |
| `\b.` repeated (two-sided boundary) | 597 | 16,318 (16,318) | 16,318 (16,318) | 0.9 | 47.9 | 0.76 | 847 / 1899 | 16.8 / 11.1 / 19.4 | 0.9 / 0.4 / 0.9 |
| `\b` repeated (two-sided, consecutive) | 439 | 16,372 (16,372) | 16,372 (16,372) | 0.8 | 51.3 | 0.85 | 1126 / 2337 | 63.3 / 1.5 / 0.3 | 34.9 / 0.5 / 37.4 |
| `(?=\w)` repeated + `a` (lookahead) | 997 | 2,826 (2,826) | 2,826 (2,826) | 1.9 | 10.8 | 0.17 | 129 / 305 | 20.4 / 43.3 / 7.6 | 65.0 / 85.9 / 51.6 |
| `(?<=\w)` repeated + `a` (lookbehind) | 995 | 2,560 (2,560) | 2,560 (2,560) | 1.9 | 8.9 | 0.16 | 112 / 264 | 7.4 / 38.0 / 7.2 | 58.7 / 61.2 / 42.3 |
| `[^\W\d]` repeated (negated set with categories) | 994 | 5,538 (5,538) | 5,538 (5,538) | 1.1 | 21.0 | 0.16 | 158 / 382 | 13.6 / 3.7 / 9.6 | 3.1 / 0.7 / 1.9 |
| ci keyword list `(?i)\b(?:…)\b` | 565 | 7,239 (16,347) | 7,239 (16,347) | 6.0 | 39.5 | 0.23 | 92 / 309 | 50.6 / 11.3 / 86.3 | 12.1 / 0.5 / 13.8 |
| ci 64 distinct 2-char sets | 260 | 1,203 (1,331) | 1,203 (1,331) | 24.2 | 5.3 | 0.03 | 19 / 65 | 9.3 / 9.0 / 9.0 | 0.5 / 0.5 / 0.5 |
| ci astral literals in sets | 934 | 14,335 (16,381) | 14,335 (16,381) | 3.0 | 17.3 | 0.19 | 147 / 579 | 13.0 / 12.3 / 12.2 | 0.5 / 0.5 / 0.6 |
| `x{1000}` repeated (unrolled minimum) | 7 | 15 (15) | 4,000 (4,000) | 0.0 | 0.2 | 0.12 | 123 / 243 | 0.1 / 0.0 / 0.2 | 0.4 / 0.4 / 0.4 |
| `\w{3}\s` × 250-style counted group | 16 | 113 (113) | 31,250 (31,250) | 0.1 | 1.1 | 0.56 | 734 / 924 | 19.3 / 20.3 / 12.1 | 2.0 / 2.0 / 1.9 |
| `(a)+` nested (deepest accepted) | 34 | 81 (81) | 32,756 (32,756) | 0.2 | 4.0 | 3.19 | 1897 / 2956 | 10.0 / 22.4 / 4.9 | 12.6 / 13.6 / 10.9 |
| `(?:a|b)+` nested (deepest accepted) | 71 | 137 (137) | 23,526 (23,526) | 0.2 | 2.6 | 1.33 | 741 / 1527 | 6.5 / 19.0 / 4.3 | 11.3 / 16.6 / 11.3 |
| literal alternation, 1000 chars | 999 | 2,607 (2,607) | 2,607 (2,607) | 2.3 | 8.0 | 0.10 | 58 / 139 | 2.9 / 0.1 / 0.0 | 0.7 / 0.0 / 0.0 |
| `(?i:a|…)*+` nested 16 (stack worst) | 145 | 477 (477) | 461 (461) | 1.0 | 3.2 | 0.04 | 24 / 55 | 244.6 / 227.8 / 188.7 | 26.2 / 24.9 / 26.5 |
| `\b\w+\s+(?:token|secret)\b` (A's rule) | 26 | 185 (185) | 274 (274) | 0.1 | 0.4 | 0.01 | 7 / 16 | 4.5 / 4.0 / 4.1 | 1.3 / 0.8 / 1.1 |
| IBAN shape | 32 | 93 (93) | 313 (313) | 0.1 | 0.2 | 0.01 | 9 / 16 | 0.0 / 0.0 / 0.5 | 0.6 / 0.3 / 0.5 |

Worst over these shapes: translate 24.2 ms, compile 51.3 ms, C call 3.19 ms, retained 1897 KiB, peak 2956 KiB.

## 3. Plans at the per-plan limits (translate + compile, cold, rule by rule)

| Plan | Rules accepted / refused by the plan sums | Translated chars | Units | Translate ms | Compile ms | Largest C call ms |
|---|---|---|---|---|---|---|
| 64 × the costliest per-character shape (`\b.` at the size cap) | 8 / 56 | 130,608 | 130,608 | 8 | 545 | 1.29 |
| 64 × `\s` at the size cap | 8 / 56 | 129,824 | 129,824 | 4 | 184 | 0.22 |
| 64 × `(a)+` nested at the units cap | 8 / 56 | 712 | 262,112 | 2 | 38 | 4.22 |
| 64 × `x{1000}` at the units cap | 64 / 0 | 1,688 | 256,728 | 5 | 23 | 0.27 |
| 64 × ci 64 distinct sets (512 per-plan ci items) | 8 / 56 | 9,415 | 9,415 | 202 | 46 | 0.04 |

## 4. The parse-only check (projector, control): `check_plan`, thread CPU

| Plan | Rev. 5 `check_limits` × 64, ms | Rev. 6 `check_plan` ms (cold caches) | Rev. 6 per rule worst ms |
|---|---|---|---|
| 64 rules: 1000-char `\b`-dense ci patterns (each boundary asks `re` for a verdict) | 115.8 | 513.6 | 16.7 |
| 64 rules: 990-char literal alternations | 78.8 | 149.2 | 2.4 |
| 64 rules: `\b.`×330 (refused) | 54.1 | 233.1 | 3.9 |
| 64 rules: 499-deep bombs (refused) | 76.5 | 0.6 | 0.0 |
| 64 rules: (?i) + 64 distinct 2-char sets | 17.0 | 38.8 | 0.9 |

Host: 2 shared CPUs, load average 4.2 at the end; thread CPU throughout.
