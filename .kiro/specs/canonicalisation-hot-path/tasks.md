# Task 1F — tasks

- [x] 1F.1 Profile a guard pass; locate the cost (`canonicalize_for_detection` = 67%,
      2 `unicodedata.category` calls per character, same text canonicalised 3× per scan)
- [x] 1F.2 Equivalence gate built BEFORE the change: a verbatim frozen copy of the
      pre-1F implementation as the reference, not a snapshot of outputs
- [x] 1F.3 Factor `_canon_char`; GENERATE `_ASCII_IDENTITY` from it so the table cannot
      drift from the rules it mirrors
- [x] 1F.4 ASCII identity fast path + `lru_cache(maxsize=32)` on the public entry point
- [x] 1F.5 R1/R2: identical over every code point <0x2000, all 748 corpus items, and 17
      adversarial Unicode classes; `index_map` contract holds
- [x] 1F.6 R3: streaming gate vs the **pre-1E** baseline — 748 + 2244 byte-identical
- [x] 1F.7 R4: 1.57× / 1.72× ASCII, 1.38× / 1.42× Unicode; nothing regressed
- [x] 1F.8 Full gateway suite: 240 before, 240 after — zero new, zero fixed
- [ ] 1F.9 Re-measure E2E; confirm the streaming curve moves
