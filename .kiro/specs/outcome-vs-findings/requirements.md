# Requirements — separate what HAPPENED from what was NOTICED

## The gap, as stated

> "A flag records something worth attention; it does not, by itself, establish what happened
> to the traffic... The final API/UI contract should expose the actual enforcement outcome
> separately from findings or review status. Otherwise a dashboard label reading 'FLAG'
> leaves the operator unable to tell whether sensitive data reached the provider."

## What the code does today

`PipelineDecision` (`enforcement.py:183`) carries **one** `action` field, resolved through
the lattice `block 5 > redact 4 > rewrite 3 > model_downgrade 2 > monitor 1 > allow 0`.

`monitor` therefore occupies the same slot as `block` and `redact`. Two consequences:

**A finding is destroyed by a co-occurring higher action.** If a redact rule and a monitor
rule both match, `max_action` returns `redact` and the monitored finding is absent from the
decision. The operator sees "redacted" and cannot learn that an injection was also observed.
`matched_rules` carries the names but not *which rule produced the outcome* versus *which
was merely observed*.

**"monitor" answers the wrong question.** `action == "monitor"` says something was noticed;
it does not say the request was forwarded. `action == "allow"` says it was forwarded; it does
not say nothing was noticed. Neither field answers "did the sensitive bytes reach the
provider?", which is the question an operator is actually asking.

## Requirements

**R1 — One field answers the traffic question.** `outcome` ∈
`{allow, redact, rewrite, model_downgrade, block}`. It states what happened to the bytes and
nothing else. **`monitor` is not an outcome** — a monitored finding yields whatever outcome
the enforced rules produced, usually `allow`.

**R2 — Findings are a list and are never lossy.** Every rule that matched appears, whether
it was enforced or observed, with: source (policy / tier-1 / semantic), the action its
policy *would* have applied, and whether that action **was enforced or observed**. A
co-occurring higher action must not remove a lower finding. This is the defect in R1's first
consequence and is the single most important requirement here.

**R3 — Flag is an annotation, never an outcome.** A flag marks a finding for attention. It
may accompany any outcome: allowed-and-flagged, redacted-and-flagged, blocked-and-flagged.
It never appears where `outcome` appears.

**R4 — Monitor must not mean "the detector did not run".** A finding observed under a
monitor rule is recorded as *detected, not enforced*. Absence of a finding must mean the
detector ran and found nothing — distinguishable from the detector not running at all.
(The parity review found an async monitor consumer that was a stub; this must not recur.)

**R5 — Monitoring one rule does not relax another.** A monitor-mode injection rule leaves
authentication, kill switches and every PII rule fully enforced. Monitor scope is per rule,
never global.

**R6 — No defaults.** Consistent with the frozen product invariant: zero configured policy
means passthrough and an empty findings list. Nothing is flagged or enforced that the
operator did not enable. Identical on the native and OpenAI-SDK surfaces.

**R7 — Backward compatible.** `action` continues to be emitted with today's values so
existing consumers keep working. `outcome` and `findings` are additive.

## Out of scope

- The semantic detector's coverage (PG2 vs a harm taxonomy) — separate, unresolved question.
- Streaming block semantics — tracker G2.5, its own task.
