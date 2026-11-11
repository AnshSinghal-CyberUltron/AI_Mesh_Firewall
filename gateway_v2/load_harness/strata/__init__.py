"""The five GW20b traffic strata generators (B6 / R2-21).

Each stratum is a pure function of `(population/config, random.Random, arrival)` so it is
offline-deterministic and property-testable: `keys` (multi-key), `prompts` (long-prompt corpus),
`retry` (SDK-retry clients), `secret_output` (secret-shaped output), and `tenants` (tenant-scale).
"""

__all__: tuple[str, ...] = ()
