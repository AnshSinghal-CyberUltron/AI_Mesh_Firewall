## 1. Reviewer B's revision-5 cases (`limits_attack.py`), revision 5 → revision 6

| Pattern | Rev. 5: passes limits? translated chars, compile ms, match 32 KiB ms | Rev. 6: verdict | Rev. 6 estimate (size / units) |
|---|---|---|---|
| `\s`×500 | passes; 39,000 chars, compile 53 ms, match 1.2 ms | refused: translated size 39000 over 16384 | 39,000 / 39,000 |
| `\b.`×330 | passes; 27,060 chars, compile 102 ms, match 11.7 ms | refused: translated size 27060 over 16384 | 27,060 / 27,060 |
| `(?=\w)`×166 + `a` | passes; 2,826 chars, compile 6 ms, match 13.4 ms | accepted | 2,826 / 2,826 |
| `(?<=\w)`×142 + `a` | passes; 2,560 chars, compile 5 ms, match 5.4 ms | accepted | 2,560 / 2,560 |
| 400-deep groups | **RecursionError** escapes | refused: groups nested deeper than 16 | — |
| 499-deep groups (projector poison) | **RecursionError** escapes | refused: groups nested deeper than 16 | — |
| 330-deep `(?:` | Refused: pattern longer than 1000 | refused: pattern longer than 1000 | — |
| `(a)+` nested 16 (new) | passes; 116 chars, compile 166 ms, match 441.3 ms | refused: compile units 1048564 over 32768 | 116 / 1,048,564 |
| `(?:a)+` nested 24 (new) | passes; compile not run (exponential: 16 levels 0.2-0.3 s, 32 levels exhausted memory) | refused: groups nested deeper than 16 | — |
| `(a)+` nested 32 (new: OOM-killed the probe) | passes; compile not run (exponential: 16 levels 0.2-0.3 s, 32 levels exhausted memory) | refused: groups nested deeper than 16 | — |

## 2. The worst accepted shape at each limit (revision 6)

Limits: length ≤ 1000, depth ≤ 16, translated size ≤ 16,384, compile units ≤ 32,768, ci items ≤ 64, expanded repetition ≤ 1000.

| Shape | Src chars | Translated (estimate) | Units (estimate) | Translate | Compile total | of which C (GIL held) | Retained / peak KiB | Match 32 KiB: prose / word / code | `re` prose / word / code |
|---|---|---|---|---|---|---|---|---|---|
| `\s` repeated (set expansion) | 420 | 16,380 (16,380) | 16,380 (16,380) | 0.6 | 34.0 | 0.19 | 165 / 612 | 0.8 / 0.8 / 1.2 | 0.3 / 0.1 / 0.3 |
| `\b.` repeated (two-sided boundary) | 597 | 16,318 (16,318) | 16,318 (16,318) | 0.4 | 43.2 | 0.55 | 847 / 1899 | 11.4 / 8.0 / 14.1 | 0.7 / 0.3 / 0.8 |
| `\b` repeated (two-sided, consecutive) | 439 | 16,372 (16,372) | 16,372 (16,372) | 0.4 | 38.8 | 0.74 | 1126 / 2337 | 49.0 / 1.0 / 0.2 | 25.3 / 0.3 / 28.1 |
| `(?=\w)` repeated + `a` (lookahead) | 997 | 2,826 (2,826) | 2,826 (2,826) | 1.1 | 6.1 | 0.19 | 129 / 305 | 14.8 / 32.1 / 9.1 | 67.6 / 75.0 / 34.6 |
| `(?<=\w)` repeated + `a` (lookbehind) | 995 | 2,560 (2,560) | 2,560 (2,560) | 1.0 | 4.9 | 0.15 | 112 / 264 | 5.0 / 34.2 / 7.3 | 46.1 / 51.8 / 35.9 |
| `[^\W\d]` repeated (negated set with categories) | 994 | 5,538 (5,538) | 5,538 (5,538) | 0.7 | 18.5 | 0.17 | 158 / 382 | 13.3 / 4.0 / 9.9 | 2.9 / 0.6 / 1.8 |
| ci keyword list `(?i)\b(?:…)\b` | 565 | 7,239 (16,347) | 7,239 (16,347) | 7.2 | 40.9 | 0.19 | 92 / 309 | 49.1 / 11.1 / 79.7 | 11.6 / 0.3 / 9.7 |
| ci 64 distinct 2-char sets | 260 | 1,203 (1,331) | 1,203 (1,331) | 73.4 | 2.8 | 0.03 | 19 / 65 | 7.5 / 6.7 / 6.6 | 0.3 / 0.3 / 0.4 |
| ci astral literals in sets | 934 | 14,335 (16,381) | 14,335 (16,381) | 3.1 | 16.0 | 0.14 | 147 / 579 | 7.8 / 7.8 / 7.2 | 0.3 / 0.3 / 0.3 |
| `x{1000}` repeated (unrolled minimum) | 7 | 15 (15) | 4,000 (4,000) | 0.0 | 0.2 | 0.12 | 123 / 243 | 0.0 / 0.0 / 0.1 | 0.2 / 0.2 / 0.2 |
| `\w{3}\s` × 250-style counted group | 16 | 113 (113) | 31,250 (31,250) | 0.1 | 0.8 | 0.43 | 734 / 924 | 9.3 / 11.2 / 7.2 | 1.3 / 1.3 / 1.3 |
| `(a)+` nested (deepest accepted) | 34 | 81 (81) | 32,756 (32,756) | 0.1 | 3.2 | 2.40 | 1897 / 2956 | 9.1 / 19.5 / 3.8 | 9.3 / 9.7 / 7.2 |
| `(?:a|b)+` nested (deepest accepted) | 71 | 137 (137) | 23,526 (23,526) | 0.2 | 1.8 | 1.03 | 741 / 1527 | 5.9 / 17.4 / 3.7 | 10.4 / 17.2 / 11.2 |
| literal alternation, 1000 chars | 999 | 2,607 (2,607) | 2,607 (2,607) | 2.4 | 7.1 | 0.09 | 58 / 139 | 1.7 / 0.0 / 0.0 | 0.5 / 0.0 / 0.0 |
| `(?i:a|…)*+` nested 16 (stack worst) | 145 | 477 (477) | 461 (461) | 1.2 | 3.5 | 0.06 | 24 / 55 | 213.3 / 241.1 / 250.2 | 39.1 / 25.6 / 25.6 |
| `\b\w+\s+(?:token|secret)\b` (A's rule) | 26 | 185 (185) | 274 (274) | 0.1 | 0.4 | 0.01 | 7 / 16 | 4.4 / 3.8 / 3.9 | 1.2 / 0.8 / 1.0 |
| IBAN shape | 32 | 93 (93) | 313 (313) | 0.1 | 0.2 | 0.01 | 9 / 16 | 0.0 / 0.0 / 0.5 | 0.6 / 0.3 / 0.5 |

Worst over these shapes: translate 73.4 ms, compile 43.2 ms, C call 2.40 ms, retained 1897 KiB, peak 2956 KiB.

## 3. Plans at the per-plan limits (translate + compile, cold, rule by rule)

| Plan | Rules accepted / refused by the plan sums | Translated chars | Units | Translate ms | Compile ms | Largest C call ms |
|---|---|---|---|---|---|---|
| 64 × the costliest per-character shape (`\b.` at the size cap) | 8 / 56 | 130,608 | 130,608 | 6 | 335 | 1.12 |
| 64 × `\s` at the size cap | 8 / 56 | 129,824 | 129,824 | 3 | 154 | 0.13 |
| 64 × `(a)+` nested at the units cap | 8 / 56 | 712 | 262,112 | 2 | 30 | 3.80 |
| 64 × `x{1000}` at the units cap | 64 / 0 | 1,688 | 256,728 | 4 | 17 | 0.44 |
| 64 × ci 64 distinct sets (512 per-plan ci items) | 8 / 56 | 9,415 | 9,415 | 431 | 23 | 0.03 |

## 4. The parse-only check (projector, control): `check_plan`, thread CPU

| Plan | check_plan ms (cold caches) | per rule worst ms |
|---|---|---|
| 64 rules: 1000-char `\b`-dense ci patterns (each boundary asks `re` for a verdict) | 272.5 | 9.5 |
| 64 rules: 990-char literal alternations | 182.3 | 4.4 |
| 64 rules: `\b.`×330 (refused) | 316.9 | 3.7 |
| 64 rules: 499-deep bombs (refused) | 0.5 | 0.0 |
| 64 rules: (?i) + 64 distinct 2-char sets | 37.6 | 0.7 |

Host: 2 shared CPUs, load average 8.0 at the end; thread CPU throughout.
