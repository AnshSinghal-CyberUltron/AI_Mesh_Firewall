# Design — `outcome` + `findings`, additive to `action`

## Shape

```python
@dataclass(frozen=True)
class Finding:
    source: str            # "policy" | "tier1" | "semantic" | "rate_limit" | ...
    category: str          # threat_type / rule category
    rule_id: int | None
    rule_name: str
    policy_name: str
    would_action: str      # what this rule's action IS, per policy
    enforced: bool         # True: contributed to the outcome. False: observed only.
    confidence: float | None
    flagged: bool          # marked for operator attention
```

`PipelineDecision` gains:

```python
outcome:  str              # allow | redact | rewrite | model_downgrade | block
findings: list[Finding]
action:   str              # UNCHANGED, today's lattice value — R7
```

## Why `enforced` is a per-finding boolean, not a second lattice

The temptation is a second enum ("observed action" vs "enforced action"). That reintroduces
the same fault at one remove: two enums still cannot express *three* rules where one blocked,
one redacted and one was observed. A per-finding boolean scales to any number of findings and
makes R2's non-lossiness structural rather than something to be tested for.

## Deriving `outcome` from `findings`

`outcome = max_action(f.would_action for f in findings if f.enforced)`, defaulting to
`allow`. A monitor-mode rule sets `enforced=False`, so it **cannot** raise the outcome — and
because it is still in the list, it cannot be lost either. That is R2 and R3 in one rule.

`monitor` never appears as an `outcome`. Where the lattice would have produced `monitor`
today, the outcome is `allow` and the finding carries `enforced=False, flagged=True`.

## The invariant worth stating plainly

**`outcome` alone must answer: did the sensitive bytes reach the provider?**

- `allow` — the bytes were forwarded as supplied.
- `redact` / `rewrite` — the **transformed** bytes were forwarded; the originals were not.
- `block` — nothing was forwarded.

No value of `findings` changes that reading. An operator who reads only `outcome` is never
misled, and an operator who reads only `findings` never mistakes a finding for an action.

## "Detector ran and found nothing" vs "detector did not run" (R4)

An empty `findings` list is ambiguous on its own. `PipelineDecision` gains
`detectors_run: list[str]`, so absence of a finding from a detector that ran means clean,
and absence of the detector itself means it did not run. This is the same distinction
tracker **G1.4** demands for stage latency ("a 0 ms stage and a skipped stage are not the
same event") and the two should not be solved differently.

## A second, separable defect found while reading this file

`stage_latency_ms: int = 0` with `elapsed = int((...) * 1000)` truncates every duration
below 1 ms to zero, and `as_dict` emits the key only `if self.stage_latency_ms:` — so a
stage that ran in 0.4 ms reports **no latency at all**, which reads as "did not run". That
is G1.4 exactly. Fixing it is a one-line change to a float, but it alters the trace schema,
so it is tracked as its own task rather than smuggled in here.

## Migration

`action` keeps its current derivation, so no consumer breaks. `outcome`/`findings` are read
by new consumers. When every consumer has moved, `action` can be retired — not in this task.
