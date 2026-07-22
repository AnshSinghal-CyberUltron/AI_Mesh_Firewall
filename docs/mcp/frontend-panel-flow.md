# Frontend MCPConnectorPanel — flow + the 4 bug sites

> Deliverable for `scripts/ralph/mcp_progress.md` **P0 item #4**: map
> `frontend/src/components/MCPConnectorPanel.jsx` (2223 lines) register/authorize/list/execute +
> pinpoint the B1/B2/B4 bug sites. All anchors read + spot-verified (commit `8f952afd`).
>
> ⚠️ **Key finding:** B1, B2, and B4 all carry SUBSTANTIAL PRIOR FIXES already in the code
> (fix comments reference "MCP OAuth bugs #1/#2", "bug #3", "FOCUS-LOSS ROOT FIX"; the git status
> shows a deleted `iter1-bug5-modal-focus-fixed.png`). Per CLAUDE.md, symptoms ≠ current state — this
> map records what is ACTUALLY in the code now and what (if anything) remains, so the fix items
> (#13-18) are scoped as *verification-first*, not blind re-implementation. Feeds item #5.

## Component structure (why there is no remount bug)
- `MCPConnectorPanel` (`:2217`) → `MCPConnectorPanelInner` (`:303`), wrapped in a provider.
- Render helpers are plain arrow fns returning JSX, invoked as **function calls** — `renderServers()`,
  `renderTools()`, `renderExecute()`, … at `:2203-2208`; `renderServerCard(srv)` (`:1074`) via map.
  This is the SAFE pattern: calling a function inline ≠ mounting a component, so React reconciles by
  position and preserves input DOM nodes/focus across re-renders.
- **There is NO component defined inside render** (only the two top-level `function` decls at `:303`
  and `:2217`) — the classic focus-loss anti-pattern is ABSENT.
- Modal uses `Dialog`/`DialogHeader`/`DialogBody`/`DialogFooter` (stable top-level exports of
  `ui/Dialog.jsx`); `Dialog` is `createPortal`, returns `null` when closed.

## Register (the Add Server modal) — `:1347-1633`
- `<Dialog open={addOpen} onClose={() => setAddOpen(false)}>` (`:1347`); state `addForm` (`:315`,
  `makeEmptyAddForm:115`); submit `addServer` (`:478`) → `POST /api/mcp-connector/servers/`;
  presets via `registerPreset` (`:527`). `buildServerPayload:151`.
- Fields are plain controlled `<input value={addForm.X} onChange={e => setAddForm({...addForm, X:...})}>`
  (name `:1362`, url `:1371`, transport `Select :1380`, description `:1412`, stdio command/args/env
  `:1435-1465`, auth token/basic/authheaders/query_param `:1500-1616`).
- Footer Register button disabled until name + (command for stdio / url otherwise) (`:1627`).

## Authorize — two mutually-exclusive paths
| Helper | Condition | Button → handler | Endpoint |
|---|---|---|---|
| `serverNeedsOAuth` (`:711`) | `transport==stdio` && args contain `mcp-remote` | `:1166` → `startOAuth` (`:773`) | **gateway** `POST /gateway/{org}/mcp/{slug}/oauth/start` (`:804`) |
| `serverUsesHttpOAuth` (`:726`) | `auth_type==oauth` && `url` && `transport∈(streamable-http,sse)` | `:1180` → `startControlOAuth` (`:845`) | **control** `POST /api/mcp-connector/servers/{id}/oauth/authorize/` (`:855`) |

The two conditions are mutually exclusive (one requires stdio, the other requires HTTP), so **exactly
one (or zero) Authorize button renders per card.** `startOAuth` extracts the mcp-remote URL
(`extractMcpRemoteUrl:760`) and opens a popup; `startControlOAuth` opens a popup, then polls the
server detail for `oauth_authorized` every 3s (≤180s) and **auto-syncs tools on success** (`:883`),
with interval cleanup on success/timeout/unmount (`:864/896/906`).

## List / cards — `renderServers` (`:1307`) / `renderServerCard` (`:1074`)
- `loadServers` (`:356`) GET `/servers/`; card shows connection badge, `tools_count` (`:1102`),
  `last_sync_at`, `last_sync_error` (`:1113`), gateway endpoint + copy-config, per-tool controls
  (expand → `loadServerTools:433`), sync (`syncServerTools:592`), delete.
- Sync button disabled when `syncBlockedForAuth(srv)` (`:757` = `auth_type==oauth && !oauth_authorized`) (`:1211`).

## Execute — `renderExecute` (`:1743`)
- `loadTools` (`:388`) GET `/tools/`; select server/tool, JSON args (`executeArguments` `:325`), run →
  `POST /api/mcp-connector/tools/call/`; result preview via `extractExecutionPreview:230` /
  `formatExecutionError:214`.

## The 4 bug sites — current state

### B1 — oauth+stdio / two buttons / "Server has no URL"
- **Form guard PRESENT:** the `oauth` auth-type option is filtered out unless
  `transport∈(streamable-http,sse)` (`:1483-1490`); switching transport to non-HTTP drops
  `auth_type` oauth→none (`:1385-1392`). So **oauth is not selectable for stdio/websocket in the form.**
- **Exactly-one-button PRESENT:** `serverNeedsOAuth`/`serverUsesHttpOAuth` are mutually exclusive
  (`:1166` vs `:1180`); `serverUsesHttpOAuth` requires `!!srv.url` so the control button never shows
  for a URL-less row → "Server has no URL" is unreachable *from the button*.
- **REMAINING (item #13):** the **duplicate/broken control authorize path still exists** end-to-end:
  frontend `startControlOAuth` (`:845`) → control `MCPServerOAuthStartView` (`views.py:2513`) which
  emits `400 "Server has no URL"` (`views.py:2526`) and sets `auth_type="oauth"` bypassing the
  registration guard (`views.py:2582`, see `control-plane-flow.md`). B1 spec = *delete* that path so
  it's structurally unreachable (route via the gateway path only), not just guard the button. Decide
  in #13 whether http-oauth should route through the gateway `oauth/start` too (unifying to one path).

### B2 — fresh HTTP-oauth server shows 0-tools card instead of pending
- **Pending state PRESENT:** `serverAwaitingAuth` (`:741`) → renders `Badge "authorization required"`
  (`:1095-1097`) for `auth_type==oauth && !oauth_authorized` (and for stdio-mcp-remote with 0 tools +
  not connected). `syncBlockedForAuth` gates premature sync (`:1211`). Backend supplies the
  `oauth_authorized` signal (`models.py:178`).
- **REMAINING (item #15):** in-browser verification that a freshly-registered HTTP-oauth server
  renders the pending badge (not a plain "0 tools / Unknown" card) and that tools populate after
  authorize+sync. The badge is a *supplement* to the normal card, not a replacement — confirm the UX
  reads clearly as "pending" (task wants a "distinct Pending authorization state").

### B4 — Add Server modal loses focus per keystroke
- **Dialog-level fix PRESENT:** `ui/Dialog.jsx` keeps `onClose` in a ref (`:20-24`) so `handleKey`
  is stable (`useCallback([])` `:26`) and the focus-trap/mount effect deps are `[open, handleKey]`
  (`:69`) — it re-runs only when `open` toggles, NOT per keystroke (the effect's cleanup previously
  stole focus by re-autofocusing the first element).
- **No in-render component** (confirmed) and render helpers are function calls, so the input DOM nodes
  are preserved across the `setAddForm` re-render. **At the code level B4's known root causes are
  addressed.**
- **REMAINING (item #17/18):** in-browser Playwright confirmation that typing a long string into each
  field keeps focus per keystroke. Minor nit to watch: auth-header rows use index-based keys
  (`key={`auth-header-${index}`}` `:1534`) — fine for append/edit, could reorder-glitch on
  delete-middle (not a focus-per-keystroke issue).

## Verification
Spot-verified (all exact): `MCPConnectorPanel.jsx` 115,151,303,315,356,388,478,527,592,711,726,741,757,760,773,845,855,883,906,1074,1079,1095,1166,1180,1211,1307,1347,1362,1380,1385,1420,1478,1483,1627,1637,1743,2203-2208,2217; `ui/Dialog.jsx` 10,20,26,69,95,120,124. Cross-refs: `control/.../views.py:2513/2526/2582`, `docs/mcp/control-plane-flow.md`.
