# P1.3 — Add-Server dialog focus loss (B4)

**Date:** 2026-07-02  
**Agent:** cursor-ralph-iter3  
**Stack:** frontend :8180  

## Goal

Open Register MCP Server dialog, type long strings into every field, capture **focus loss after 1
keystroke** (B4).

## Method

1. Playwright: `scripts/playwright_mcp_p1_dialog_focus_repro.mjs` with
   `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium-browser`
2. Per-field: click field → type 24 chars via `keyboard.press` → after each key compare
   `document.activeElement` to the target input
3. Fields exercised: **name**, **url**, **description**, **stdio command** (transport=stdio),
   **bearer token** (transport=streamable-http, auth=bearer)

## Result: B4 **NOT reproducing**

| Field | Keystrokes | Focus kept | Final value len |
|-------|------------|------------|-----------------|
| name | 24 | yes | 24 |
| url | 24 | yes | 24 |
| description | 24 | yes | 24 |
| stdio-command | 24 | yes | 24 |
| bearer-token | 24 | yes | 24 |

`b4BugConfirmed: false`, `focusLossFields: []`

## Root cause (prior fix — code trace)

The historical B4 failure mode is documented in `frontend/src/components/ui/Dialog.jsx`:

- Inline `onClose={() => setAddOpen(false)}` in `MCPConnectorPanel` created a new function each render
- If the focus-trap `useEffect` depended on `onClose`, it re-ran on every `setAddForm` keystroke
- Cleanup called `prevFocus.focus()` and re-autofocused the first focusable element → focus stolen

**Fix present:** `onCloseRef` + stable `handleKey` (`useCallback([])`); effect deps `[open, handleKey]`
only — runs on open toggle, not per keystroke (`Dialog.jsx:14-68`).

Render helpers in `MCPConnectorPanel` are function **calls** (`{renderServers()}`), not inline
component definitions — inputs are not remounted on parent re-render.

## Artifacts

- Screenshots: `mcp-parallel/findings/p1-3/01-dialog-open.png` … `06-after-bearer-typing.png`
- JSON report: `mcp-parallel/findings/p1-3/report.json` (per-keystroke `keptFocus` trace)
- Network: `mcp-parallel/findings/p1-3/network.jsonl`
- Repro gate: `scripts/playwright_mcp_p1_dialog_focus_repro.mjs` → **PASS** (exit 0)

## Conclusion

B4 focus-loss **does not reproduce** on the live stack with current frontend. P5.16 should remain
**verification-first** (keep Playwright gate); no code change required this iteration. Minor watch:
auth-header rows use index keys — reorder on delete-middle only, not per-keystroke focus.
