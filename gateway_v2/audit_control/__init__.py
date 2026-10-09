"""The durable audit path: a Postgres sink, and an exporter that makes loss exact.

GW14c requires *"a durable audit sink with an acknowledged-vs-durable high-water mark and
`records_lost`"*. The budget in `gateway_v2/audit/` protects the STORE; it does not make audit
durable — `rc3-audit-mem-v1`'s own README says so: *"Trimmed records are gone from the store, and
they are counted. ... Until then, export must drain faster than the trim rate."* This package is
that drain, and the thing that notices when it loses the race.

It is a separate process for the same reason R2-04 made the re-hydrator one: the requirement is
about a running component, and there was nothing to attach it to. `gateway_v2/edge/app.py` is
still a stub, so an in-gateway drain would be undeployable and unprovable; and the drain's
position — the durable high-water mark — has to survive a worker restart, which makes a
per-worker background task the wrong home for it regardless.

Why Postgres, as the card instructs ("choose and price at GW14c"): Cloud SQL PostgreSQL 16 HA is
already in the signed fleet, `state_control/pg.py` already carries the R2-04-hardened bounded
sessions, and round 2 measured its failure behaviour (failover 11.6-15.5 s loaded, 0 acknowledged
writes lost). GCS and BigQuery each need an adapter this repository does not have. The pricing
arithmetic is in `docs/plans/2026-10-08-r2-05-gw14c-audit-memory-and-durability.md`.
"""

__all__: tuple[str, ...] = ()
