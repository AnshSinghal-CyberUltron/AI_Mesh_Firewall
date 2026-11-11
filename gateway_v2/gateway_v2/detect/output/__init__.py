"""GW33 output-parity detectors: IP leak, exfiltration channels, canary, grounding.

Every detector emits `Finding[]` only (never a disposition), carries `evidence=None`, stamps its
platform rule id on `Finding.detector` (D-32), turns any internal exception or budget miss into ONE
`UNAVAILABLE` finding (D-18), and takes its numbers from a single `OutputBounds` (D-30).
"""
