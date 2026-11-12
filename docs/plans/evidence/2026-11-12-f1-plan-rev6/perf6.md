## 1. Reviewer B's revision-5 cases (`limits_attack.py`), revision 5 → revision 6

| Pattern | Rev. 5: passes limits? translated chars, compile ms, match 32 KiB ms | Rev. 6: verdict | Rev. 6 estimate (size / units) |
|---|---|---|---|
| `\s`×500 | passes; 39,000 chars, compile 47 ms, match 1.0 ms | refused: translated size 39000 over 16384 | 39,000 / 39,000 |
| `\b.`×330 | passes; 27,060 chars, compile 77 ms, match 13.0 ms | refused: translated size 27060 over 16384 | 27,060 / 27,060 |
| `(?=\w)`×166 + `a` | passes; 2,826 chars, compile 7 ms, match 15.9 ms | accepted | 2,826 / 2,826 |
| `(?<=\w)`×142 + `a` | passes; 2,560 chars, compile 5 ms, match 5.7 ms | accepted | 2,560 / 2,560 |
| 400-deep groups | **RecursionError** escapes | refused: groups nested deeper than 16 | — |
| 499-deep groups (projector poison) | **RecursionError** escapes | refused: groups nested deeper than 16 | — |
| 330-deep `(?:` | Refused: pattern longer than 1000 | refused: pattern longer than 1000 | — |
| `(a)+` nested 16 (new) | passes; 116 chars, compile 162 ms, match 439.3 ms | refused: compile units 1048564 over 32768 | 116 / 1,048,564 |
| `(?:a)+` nested 24 (new) | passes; compile not run (exponential: 16 levels 0.2-0.3 s, 32 levels exhausted memory) | refused: groups nested deeper than 16 | — |
| `(a)+` nested 32 (new: OOM-killed the probe) | passes; compile not run (exponential: 16 levels 0.2-0.3 s, 32 levels exhausted memory) | refused: groups nested deeper than 16 | — |

## 2. The worst accepted shape at each limit (revision 6)

Limits: length ≤ 1000, depth ≤ 16, translated size ≤ 16,384, compile units ≤ 32,768, ci items ≤ 64, expanded repetition ≤ 1000.

| Shape | Src chars | Translated (estimate) | Units (estimate) | Translate | Compile total | of which C (GIL held) | Retained / peak KiB | Match 32 KiB: prose / word / code | `re` prose / word / code |
|---|---|---|---|---|---|---|---|---|---|
| `\s` repeated (set expansion) | 420 | 16,380 (16,380) | 16,380 (16,380) | 0.3 | 19.0 | 0.15 | 165 / 612 | 0.9 / 0.8 / 1.4 | 0.3 / 0.2 / 0.4 |
| `\b.` repeated (two-sided boundary) | 597 | 16,318 (16,318) | 16,318 (16,318) | 0.5 | 40.5 | 0.64 | 847 / 1899 | 13.0 / 8.5 / 15.5 | 0.7 / 0.3 / 0.8 |
| `\b` repeated (two-sided, consecutive) | 439 | 16,372 (16,372) | 16,372 (16,372) | 0.4 | 39.9 | 0.74 | 1126 / 2337 | 52.4 / 1.0 / 0.2 | 26.9 / 0.3 / 29.3 |
| `(?=\w)` repeated + `a` (lookahead) | 997 | 2,826 (2,826) | 2,826 (2,826) | 1.1 | 6.3 | 0.13 | 129 / 305 | 15.9 / 34.4 / 6.3 | 50.0 / 62.0 / 37.8 |
| `(?<=\w)` repeated + `a` (lookbehind) | 995 | 2,560 (2,560) | 2,560 (2,560) | 1.2 | 5.3 | 0.09 | 112 / 264 | 6.0 / 29.5 / 5.4 | 42.1 / 61.4 / 35.4 |
| `[^\W\d]` repeated (negated set with categories) | 994 | 5,538 (5,538) | 5,538 (5,538) | 0.6 | 16.7 | 0.17 | 158 / 382 | 10.3 / 3.1 / 7.4 | 2.3 / 0.5 / 1.4 |
| ci keyword list `(?i)\b(?:…)\b` | 565 | 7,239 (16,347) | 7,239 (16,347) | 3.6 | 20.8 | 0.15 | 92 / 309 | 39.1 / 8.0 / 66.0 | 8.8 / 0.4 / 10.4 |
| ci 64 distinct 2-char sets | 260 | 1,203 (1,331) | 1,203 (1,331) | 16.9 | 3.2 | 0.03 | 19 / 65 | 6.7 / 6.8 / 6.6 | 0.3 / 0.3 / 0.3 |
| ci astral literals in sets | 934 | 14,335 (16,381) | 14,335 (16,381) | 1.7 | 17.9 | 0.15 | 147 / 579 | 9.7 / 9.6 / 9.1 | 0.4 / 0.4 / 0.4 |
| `x{1000}` repeated (unrolled minimum) | 7 | 15 (15) | 4,000 (4,000) | 0.0 | 0.3 | 0.13 | 123 / 243 | 0.1 / 0.0 / 0.1 | 0.3 / 0.3 / 0.3 |
| `\w{3}\s` × 250-style counted group | 16 | 113 (113) | 31,250 (31,250) | 0.1 | 0.9 | 0.42 | 734 / 924 | 11.1 / 12.5 / 7.9 | 1.5 / 1.5 / 1.3 |
| `(a)+` nested (deepest accepted) | 34 | 81 (81) | 32,756 (32,756) | 0.1 | 3.0 | 2.34 | 1897 / 2956 | 10.0 / 21.9 / 4.3 | 8.2 / 10.5 / 6.9 |
| `(?:a|b)+` nested (deepest accepted) | 71 | 137 (137) | 23,526 (23,526) | 0.2 | 1.9 | 1.03 | 741 / 1527 | 5.3 / 15.8 / 3.2 | 7.4 / 10.6 / 9.2 |
| literal alternation, 1000 chars | 999 | 2,607 (2,607) | 2,607 (2,607) | 1.3 | 4.4 | 0.06 | 58 / 139 | 2.1 / 0.0 / 0.0 | 0.6 / 0.0 / 0.0 |
| `(?i:a|…)*+` nested 16 (stack worst) | 145 | 477 (477) | 461 (461) | 0.7 | 2.3 | 0.04 | 24 / 55 | 184.7 / 178.6 / 192.1 | 29.2 / 28.4 / 29.1 |
| `\b\w+\s+(?:token|secret)\b` (A's rule) | 26 | 185 (185) | 274 (274) | 0.1 | 0.4 | 0.01 | 7 / 16 | 4.9 / 4.2 / 4.3 | 1.3 / 1.1 / 1.1 |
| IBAN shape | 32 | 93 (93) | 313 (313) | 0.1 | 0.2 | 0.01 | 9 / 16 | 0.0 / 0.0 / 0.5 | 0.6 / 0.3 / 0.6 |

Worst over these shapes: translate 16.9 ms, compile 40.5 ms, C call 2.34 ms, retained 1897 KiB, peak 2956 KiB.

## 3. Plans at the per-plan limits (translate + compile, cold, rule by rule)

| Plan | Rules accepted / refused by the plan sums | Translated chars | Units | Translate ms | Compile ms | Largest C call ms |
|---|---|---|---|---|---|---|
| 64 × the costliest per-character shape (`\b.` at the size cap) | 8 / 56 | 130,608 | 130,608 | 4 | 332 | 1.06 |
| 64 × `\s` at the size cap | 8 / 56 | 129,824 | 129,824 | 3 | 154 | 0.15 |
| 64 × `(a)+` nested at the units cap | 8 / 56 | 712 | 262,112 | 2 | 32 | 4.07 |
| 64 × `x{1000}` at the units cap | 64 / 0 | 1,688 | 256,728 | 4 | 16 | 0.22 |
| 64 × ci 64 distinct sets (512 per-plan ci items) | 8 / 56 | 9,415 | 9,415 | 134 | 26 | 0.03 |

## 4. The parse-only check (projector, control): `check_plan`, thread CPU

| Plan | Rev. 5 `check_limits` × 64, ms | Rev. 6 `check_plan` ms (cold caches) | Rev. 6 per rule worst ms |
|---|---|---|---|
| 64 rules: 1000-char `\b`-dense ci patterns (each boundary asks `re` for a verdict) | 59.0 | 299.8 | 9.7 |
| 64 rules: 990-char literal alternations | 87.7 | 166.2 | 3.2 |
| 64 rules: `\b.`×330 (refused) | 57.0 | 258.3 | 4.4 |
| 64 rules: 499-deep bombs (refused) | 92.3 | 1.6 | 0.0 |
| 64 rules: (?i) + 64 distinct 2-char sets | 29.1 | 61.6 | 0.8 |

Host: 2 shared CPUs, load average 3.4 at the end; thread CPU throughout.
