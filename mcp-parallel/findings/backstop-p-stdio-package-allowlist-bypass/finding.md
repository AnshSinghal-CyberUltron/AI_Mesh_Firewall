# CHG-0126 — stdio package allowlist/pinning bypass: `--flag=value` form + 2nd package flag skipped the check

**Change-id:** CHG-0126
**Date:** 2026-07-03
**Severity:** MEDIUM (supply-chain — an unlisted / unpinned npm/PyPI package could be fetched+run past the on-demand allowlist and the pinned-version policy, on BOTH the sandbox agent and the shared gateway HOST — "no unknown npm on host"). Latent unless `MCP_STDIO_PACKAGE_ALLOWLIST` / `MCP_STDIO_REQUIRE_PINNED_PACKAGES` is set, but on that path it silently defeats the control for whole argument shapes.
**Area:** HARDEN THE ARCHITECTURE — no unknown npm on host / on-demand package allowlist (item 8), pinned reproducible fetches.
**Files:**
- `services/mcp-broker/sandbox-image/agent/stdio_manager.py` (`_extract_package_spec` → `_extract_package_specs`; enforcement loop) + `tests/test_stdio_manager_packages.py` (+8 tests).
- `gateway/ai_mesh_gateway/mcp_stdio_adapter.py` (same fix, parity) + `tests/test_mcp_stdio_adapter_package_gating.py` (new, +7 tests).
**Whose work it touches:** the owning-session stdio package-gating (N2/N3, CHG-0022 propagation). Both the sandbox-agent and gateway-host adapters — the two are explicitly meant to have *identical* supply-chain hardening.

## Root cause

Both adapters extracted the package spec with a single-spec, **space-separated-only** parser:

```python
for tok in it:
    if tok in ("--from", "--with", "--package", "-p"):
        return next(it, None)          # only the SPACE form, only the FIRST match
    if tok.startswith("-") or tok in skip:
        continue
    return tok
```

Two bypasses of the allowlist (N3) + pinned-version (N2) guards:

1. **`--flag=value` (equals) form.** `npx --package=evil-pkg safe-cmd` — the token `--package=evil-pkg`
   is not equal to `"--package"`, and it `startswith("-")`, so it is **skipped as a generic flag**. Extraction
   then returns the trailing `safe-cmd` (the command to run), so the allowlist/pinned check runs against the
   WRONG token while npx actually fetches `evil-pkg`. Same for `uvx --from=<pkg>` / `--with=<pkg>`.
2. **A second package flag.** `npx -p allowed -p evil cmd` — extraction returns only the first (`allowed`);
   `evil` is fetched but never checked.

Result: an unlisted or unpinned package is pulled from npm/PyPI and executed — in the per-org sandbox, and
(via `mcp_stdio_adapter`) on the **shared gateway host** itself.

## The fix (CHG-0126)

`_extract_package_specs` (plural) returns **every** spec the invocation fetches:
- recognizes both `--flag value` and `--flag=value` for all of `--from` / `--with` / `--package` / `-p`;
- collects **all** package-flag values (multiple flags);
- takes the bare positional as the package **only when no package flag supplied one** (with a package flag
  present, the positional is the *command* npx/uvx runs from the flagged package — so it is NOT a package and
  must not be mis-checked/over-blocked).

Enforcement now **loops over every returned spec**, applying the allowlist + pinned guards to each. A thin
`_extract_package_spec` (singular, first spec) wrapper preserves the existing helper API/tests. The same fix is
applied to both adapters for parity.

## Verification

```
# sandbox agent
cd services/mcp-broker/sandbox-image/agent
../../.venv/bin/python -m pytest tests/test_stdio_manager_packages.py -q    # 41 passed
../../.venv/bin/python -m pytest tests -q                                    # 75 passed (full agent suite)
# gateway host adapter
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_stdio_adapter_package_gating.py -q   # 7 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                             # 1794 passed, 0 failed
```

New tests assert: `--package=evil safe-cmd` → `["evil"]` (not the command); `-p a -p evil cmd` → `["a","evil"]`;
`-p realpkg runcmd` → `["realpkg"]` (command not mis-classified); and end-to-end that the smuggled package is the
one flagged as not-in-allowlist / must-be-pinned.

## Scope / honesty note

Closes the allowlist/pinned BYPASS for these argument shapes on both stdio paths. The controls remain OFF by
default (opt-in via env); this fix makes them actually enforce what they claim when enabled. Does not change the
host-blocked live-stress status (items 14–19). Partial coverage is not completion.
