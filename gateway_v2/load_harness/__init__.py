"""B6 / R2-21 — load harness v2.3: the open-loop Poisson load-and-measurement instrument.

The local half of the GW20b capacity instrument for the `gateway_v2` serving path. It generates
open-loop Poisson (and constant) arrivals under an injected clock + injected `random.Random`,
mixes in the five GW20b strata (multi-key, long-prompt, SDK-retry, secret-shaped output,
tenant-scale), collects client-side C4, climbs a ≤ 10%-step rate ladder with 3 repeats, and
reports the GW20b per-run fields — then drives a local dry-run against one gateway booted by B1's
`build_app` / B3's `serve.py` with B4's fake guard.

Placement (Req 14.1). This is its own top-level package, a sibling of `state_control/` and
`audit_control/`, deliberately OUTSIDE the `gateway_v2` import-linter serving layers
(`root_packages = ["gateway_v2"]` is left unchanged). It is an offered-load client and an offline
generator, not a serving-path layer.

The one allowed import edge. The harness imports DOWNWARD into `gateway_v2` only — specifically
`gateway_v2.runtime.latency_slo` (C4 / Firewall_Added_Delta reconciliation, Req 9) and
`gateway_v2.runtime.resources.ResourceContract` (the pass bound, Req 9.3) — exactly as
`state_control` imports `gateway_v2.domain.state_knobs`. `gateway_v2` NEVER imports `load_harness`,
so no serving-path edge is added and `lint-imports` stays contracts-kept. The harness holds no
capacity-position literal (the pass bound is read from `ResourceContract.target_p99_ms`) and no
module-level mutable; its value types are frozen + slots.
"""

__all__: tuple[str, ...] = ()
