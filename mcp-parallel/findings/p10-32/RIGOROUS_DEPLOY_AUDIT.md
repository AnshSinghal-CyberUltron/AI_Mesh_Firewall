# Item #32 rigorous verification — deployment-drift audit (committed vs live)

Triggered by the B1 fake-green (item#13 committed but not deployed). Re-checked
that EVERY fix file for items 1–31 is actually running in its container.

| file | item(s) | container | src vs live |
|------|---------|-----------|-------------|
| control mcp_connector/views.py | B1 #13 | control-1 | MATCH (hot-deployed this session) |
| control mcp_connector/serializers.py | B1 #13 | control-1 | MATCH |
| frontend MCPConnectorPanel (bundle) | B2/B4 #15/#16 | frontend-1 | "Pending authorization" present in /app/dist/assets/index-*.js + Playwright-verified |
| gateway mcp_sandbox_client.py | B3 #19/#20 | gateway-1 | MATCH |
| gateway mcp_stdio_adapter.py | P7.23 | gateway-1 | MATCH |
| shared mcp_stdio_common.py | P7.25 env-iso | gateway-1 | MATCH |
| broker docker_manager.py | P7 #22 / P8 #27 nproc | broker | live (verified item#27) |

**All item 1–31 fix files are deployed.** The one drift found — gateway
`mcp_proxy.py` — is NOT an item-1..32 file: it is the **backstop loop's G2 item 2**
(fail-CLOSED result-scan on scanner error), committed as "partial" WIP and not yet
deployed. The DEPLOYED proxy correctly serves all my isolation/scale items (P9 gate
PASS 3× live). Flagged here for the backstop loop; NOT deployed by this loop
(their in-progress change).

## Conclusion
Items 1–32 are [x] AND live-deployed AND behavior-verified (P9 gate 3× all-green +
oauth 3× + in-process gateway 38/13 & broker 95). The B1 fake-green was the only
deployment gap in this checklist; it is fixed. No further gaps found.
