# CHG-0079 — MCP tier-1 scan didn't deobfuscate invisible/confusable unicode (zero-width / homoglyph smuggling)

**Change-id:** CHG-0079
**Date:** 2026-07-02
**Severity:** MEDIUM–HIGH (a malicious upstream hides an injection / secret / internal-IP with zero-width or homoglyph unicode; a markdown/model client reads the deobfuscated value → bypasses the MCP firewall)
**Area:** HARDEN 1.4 — obfuscation-bypass parity (invisible/confusable unicode channel)
**Files:** `gateway/ai_mesh_gateway/mcp_scan_orchestrator.py` (+ `tests/test_mcp_unicode_deobfuscation.py`)
**Whose work it touches:** the MCP scan orchestrator tier-1 (CHG-0074..0078 lineage); reuses the chat scanner's `_normalize_unicode`. Extends CHG-0076 (text-encoding) to a second obfuscation channel.

## How it was found (devil's-advocate + FP-grounding of the CHG-0078 follow-up)

CHG-0078 raised MCP injection detection to chat-parity. The queued follow-up was output-injection
enforcement (drop poisoned tool descriptions). An FP probe KILLED the heuristic-drop approach: a legit
security tool "Detects jailbreak attempts and prompt injection" trips the injection patterns (FP), while
the classic `<IMPORTANT>…read ~/.ssh/id_rsa…` poison is missed by both — so dropping tools on
injection-match is wrong. The clean signal is OBFUSCATION with no legit use. Probing further: the chat
scanner deobfuscates via `_normalize_unicode` before scanning; the MCP tier-1 scanned RAW text.

## Gap

`mcp_scan_orchestrator._scan_text_tier1` ran `_injection_match` + `detect_secrets/…/ip_leakage` on the RAW
text only. Invisible/confusable unicode smuggling — zero-width chars (`I​g​n​o​r​e…`),
bidi-override, homoglyphs (`Ｉgnore…`, Cyrillic look-alikes), the Unicode-tag block, combining marks —
dodged the raw regexes. `redact_all` also does NOT strip zero-width. So a zero-width-broken / homoglyph
injection, or a secret / internal-IP hidden that way, bypassed the MCP firewall — while a markdown/model
client reads the deobfuscated value (the chat scanner already catches this via `_normalize_unicode`).

## Fix

`_scan_text_tier1` now computes `_deob = _normalize_unicode(text)` (decode unicode-tags → strip zero-width
& bidi → NFKC → drop combining marks → fold homoglyphs) and:
- runs `_injection_match` on `_deob` too (catches zero-width / homoglyph injection);
- adds `_deob` to the CHG-0076 hidden-secret/credential/internal-IP variant probe → an obscured secret/IP
  **blocks fail-closed** (redact_all cannot mask the obfuscated run; a `monitor` posture stays observe-only).

**ASCII fast-path:** only NON-ASCII text can carry these characters, so `text.isascii()` short-circuits
(the common case pays nothing). Local import (scanner does not import the orchestrator → no cycle).
Enforcement of injection is unchanged (block under a block posture, tag otherwise).

## Verify (detection + zero FP)

```
cd gateway
.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_unicode_deobfuscation.py -q   # 8 passed
.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                      # 1350 passed, 0 failed
```
- Zero-width + homoglyph injection → detected (block posture blocks + `prompt_injection` finding).
- Zero-width-hidden secret in a tool RESULT → blocked fail-closed (raw secret absent from egress).
- **Zero FP** on benign non-ASCII: an emoji ZWJ family (`👨‍👩‍👧`), Japanese, accented text (café/résumé/naïve),
  and plain ASCII — none blocked, none injection-flagged (the normalization is a detection-only probe; it
  never mutates the emitted bytes, so emoji/RTL/CJK are unaffected).
- Broker `-k "not websocket"` → 108 passed.

## Residual / follow-ups
- Injection enforcement under the DEFAULT `tag` posture remains tagged-but-forwarded (CHG-0078 residual);
  the heuristic-drop of poisoned tool descriptions was REJECTED here on FP grounds (legit security tools).
- Homoglyph/zero-width normalization is a detection-only probe (does not alter emitted content) — matches
  the chat scanner; a future step could additionally emit a sanitized (zero-width-stripped) result.
