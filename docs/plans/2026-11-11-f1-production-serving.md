# F1: production gateway serves real completions (GW36 S5 + per-org providers)

Status: 11 Nov 2026, **revision 5**.
- Revision 1 (`8484c571`) was challenged by five reviewers.
- Revisions 2 (`4d23ed07`) and 3 (`91785d9b`) were challenged by Plan Reviewer A (completeness) and Plan Reviewer B (security, scale and operability). Both requested changes each time.
- Revision 4 replaced the customer-regex design (§5.2) with one backed by a working prototype and measurements, and fixed the other revision-3 findings (§10).
- Revision 5 answers the revision-4 reviews (§10). §5.2 gains exact one-sided word boundaries, an incremental streaming scan, a tenant-fair scan scheduler, a parse-only projector check and new cost limits. Every change comes with exhaustive or fuzz evidence and measured costs. Building it found a new `regex` bug, which is worked around (§5.2.1).

Revision 3 changes how the plan is organised:
- **§5 is the implementable design of F1a-1 only.** Both revision-2 reviewers judged F1a-1 releasable once its own blockers were fixed, and §5 fixes them.
- **§6 and §7 are the requirements for F1a-2 and F1a-3.** Every finding from all seven reviews is captured there as a binding requirement. Each of those slices gets its own design revision and its own review on its own PR before any code. They carry too much rollout and security risk to be designed in the same pass as F1a-1.
- **§10** records each revision-2 finding and where it is handled.

## 1. Goal and exit

`AMF_PROFILE=production`, with **no dev flags**, serves chat completions for every organization, to that organization's own model connections, with the full firewall pipeline on:
- input scan, plan resolver, output scan, GW33 output detectors, customer rules;
- audit to the console;
- the kill switch and routing.

**Exit (L36-1, extended), at the end of F1a-3:**
- A valid console key gets a completion, streaming and non-streaming, from a recording upstream for each provider family shipped.
- An invalid key gets 401 `invalid_api_key`.
- A blocked prompt makes **zero** upstream calls.
- The trace, the metrics delta and the console row agree, joined by a per-run nonce in the prompt.

The run uses the production compose service.

## 2. Owner decisions (11 Nov 2026)

| # | Decision | Source |
|---|---|---|
| D1 | Feature 1 ships **all v1 providers**: openai, anthropic, azure, google, aws_bedrock, mistral, cohere, deepseek, huggingface, meta, ollama, custom | owner |
| D2 | Model API keys: "the industry standard that scales to 100k RPS while keeping security". Direction: envelope-encrypted credentials in signed state, with no control-plane call on the request path. The detailed design is part of F1a-3 (§7) | owner delegated |
| D3 | Model changes in the console reach gateways **live, within seconds** (signed-state feed) | owner |
| D4 | Models whose key comes from a server env var (`api_key_env_var`) are **refused in production** | owner |
| D5 | Build locally first, scale to this machine's limit, and keep the design hardware-independent (throughput scales with cores/workers); cloud tests later | owner |

## 3. Current behaviour (re-checked for this revision)

Production profile today (`edge/production_profile.py`, `edge/serve.py`):

| Dependency | Production today | Consequence |
|---|---|---|
| identity, plans, kill switch, admission, quota, org limits, incident gate | real, from signed state | — |
| `provider` | empty `StubProviderClient` (`serve.py:175,237`) | no completion |
| `routing`, `catalog` | unset / `empty_catalog_source` | `/v1/models` is `[]` |
| `input_detector` | `no_findings_detector` (`serve.py:242`) | **fail-open hazard** once a provider is wired |
| `error_scanner` / `output_detector` / `phase_resolvers` | `build_app` defaults: `no_error_findings`, `None`, `None` (`app.py:400,415,416`) | no base output scan. Plan rules are not enforced (`MinimalOutputResolver`). RAG stages refuse with `RAG_INTERNAL_ERROR` (`rag_stages.py:110-112`) |
| audit | `IncidentAuditTap(downstream=None)` (`incident_wiring.py:77-86`) | every record dropped |
| Publisher | `_NoAudit`; publishes the 44-series re-hydrator family (N14) | `amf_store_memory_reachable 0` is false, and the series are duplicated |
| response store | per-worker memory (`openai_responses.py:407`) | `/v1/responses` breaks across workers |
| GW35 unauth audit | unwired (no `unauth_audit` dep) | failed-credential records dropped |
| boot log | survivors of the fail-closed table, such as `input_detector -> no_findings_detector (fail-closed; identity/admission refuse)` (`serve.py:256`), plus a stale "every other line is fail-closed (S4/S5)" header (`production_profile.py:545`) | the log misdescribes production |

**Customer conditions (GW26).**
- **How they reach detectors.**
  - In dev only, through the `request_org` ContextVar (`dev_plan_feed.py:59`; set at `dev_components.py:177,188,213`).
  - `run_in_executor` does not copy it (`executor.py:115`). So on the off-loop paths a peer org is scanned with the primary org's program (N5). Those paths are `chat_stream.py:271`, `chat_stream.py:435` (STRICT_WITHHOLD with a family), `rag_stages.py:135` and the error frame (`chat_stream.py:388`).
  - `feed.conditions()` returns the latest program, not the request's pinned plan.
- **The regex engine** is Python `re`, which is Unicode-aware. It runs in a new daemon thread per match, joined with `budget_s` (`conditions.py:190-208`).
  - `re` holds the GIL, so the join cannot interrupt a catastrophic match.
  - `scan_conditions` gives every rule the **full** budget (`conditions.py:86`).
  - The worst case per request is deltas × rules × budget.
- **Customer regex runs in two more places.**
  - At compile, `rewrite_template.py:91-98` scans with a literal 0.25 s budget for REWRITE rules. A timeout there rejects the whole plan.
  - At publish, control runs `re.search(customer_pattern, template)` (`firewall_v2_publish.py:586-593`).
- **The timeout posture.** A timeout or a refused pattern refuses the request with `policy_regex_timeout` (400) (`conditions.py:84-88`, `input_enforcement.py:185`).
- **`bind_surface` call sites:** 9 calls in 6 modules:
  - `openai_chat:641`
  - `openai_nonstream:297,318`
  - `chat_stream:271,435`
  - `mcp:453,516`
  - `embeddings_input:128`
  - `rag_stages:135`

  `phase_resolvers.py:103` calls a different function, `_bind_surface`, on an `OutputResolver`. Moderations call the detector directly (`openai_misc.py:787`).
- **The streaming per-delta path runs on the event loop.** In non-strict modes the base detector runs synchronously in `_process_delta` (`egress/stream.py:548`). This is documented as a GW13 follow-up (`executor.py:37-40`). GIL release therefore does not protect the loop on that path.

## 4. Slices and rollout model

| Slice | Scope | Design status |
|---|---|---|
| **F1a-1 Pipeline safety** | Detector factory (N5), customer-regex engine (exact translator, incremental streaming scan, tenant-fair scan scheduler), production resolvers and base detectors, bounded audit sink, N14, GW35, response store, per-worker process hardening, honest boot log. Provider stays a stub | **§5, ready for implementation** |
| **F1a-2 MODEL state** | Forward-compatible stamp (its own release first), MODEL kind, projector, per-org applier, catalogue, GW29 semantics, alias grammar, filters | **§6 requirements**; own design and review before code |
| **F1a-3 OpenAI-compatible dispatch** | Credentials, SSRF, pools, parameter shaping, error mapping, observability, compose `gateway-v2-prod` | **§7 requirements**; own design and review before code |
| F1b | Azure OpenAI and Anthropic native adapters | after F1a-3 |
| F1c | AWS Bedrock (SigV4, event stream) | after F1b |
| F1d | Load and scaling curves, Valkey sharding | last |

Every slice ships as its own PR into `develop`, with:
- the full gateway_v2 suite, control suites (ratchet) and CI;
- two agent reviews and an agent merge;
- a live check where the slice is user-visible.

## 5. F1a-1 design: pipeline safety

**Exit criterion.** A production boot with no provider shows:
- **BLOCK:** a blocked prompt gets its declared refusal, makes zero provider calls, and produces a console row (nonce-joined).
- **No fail-open:** an allowed prompt reaches the stub and gets the declared operational error the stub produces today (the live test pins the exact code).
- **N5:** an off-loop scan of a peer org uses the peer's program.
- **Tenant isolation:** one tenant's catastrophic rule cannot stall another tenant on the same worker beyond a bounded slice.
- **Honest output:** `/metrics` and the boot log are honest.

No new error codes are introduced in F1a-1.

### 5.1 Detector factory (N5)

- **New production module `edge/detectors.py`** holds:
  - `DeterministicFindings`, `bind_surface` and `phase_resolver_factory`, moved out of `dev_components.py`. `dev_components` re-exports the **same objects**, because `bind_surface` checks the class with `isinstance`;
  - the shared `BASE_DETECTORS` tuple, which is on `develop` since #104; F1a-1 branches from `develop` after #105. Calibration and serving therefore time the same detectors.

  The module does not import `edge.app`, which avoids the import cycle that forces today's lazy imports.
- **Shape:** `DetectorFactory.for_request(plan, phase) -> RequestDetector`.
  - The result is a callable bound to the request's **pinned** `ExecutionPlan`, its compiled `ConditionProgram`, and the request's scan context: its org for the inline bucket and the scan scheduler, and the per-pass and per-delta budgets (§5.2.4, §5.2.5).
  - `bind_surface(detector, surface, stage)` keeps its signature and binds onto that object.
  - No ContextVar is used anywhere. `request_org` and `DevPlanFeed.conditions()` are deleted, and dev uses the same factory.
- **Program lifecycle (Reviewer 3 m1).**
  - The program is compiled when the PLAN record is applied, next to `plan_from_record`. It is stored in a per-worker `ConditionPrograms` map keyed by org, holding `(plan version, program)`.
  - It follows the plan store's lifecycle and bound: one entry per org present in the plan store, removed when the org is offboarded, and never replaced by an older version.
  - The request path only reads it, and the pinned plan's version selects the entry. If the stored entry is newer than the pinned plan, the request recompiles from the pinned plan, which is rare and bounded by the publish-time limits in §5.2.
- **Call sites that gain a plan parameter.** These signatures change: `scan_embeddings` (`embeddings_input.py:100-106`), `rag_stages.detector_for` (`:116-123`), and the error-frame scan (`chat_stream.py:388-394`). Their callers already hold the plan:
  - `openai_misc.py:368`;
  - `Session.plan` (`rag_common.py:129`);
  - `chat_stream.py:324`.
- **`plan=None` sites** (`openai_nonstream.py:290,309`, where `collect_and_enforce` defaults to `None`): pass the plan from `routes.py:298`, which already has it. No call site keeps `None` on a tenant request.
- **Moderations.** Behaviour is unchanged from dev: the org's plan is resolved (as `openai_misc.py:368` does for embeddings) and customer conditions run phase-only. Revision 2's `plan=None` would have dropped customer rules from moderations; it is withdrawn. If the plan is unavailable, moderations refuse with the same declared code as the chat surface's unresolved plan. They never fall back to built-ins only.
- **RAG tier-1 detectors.** `RagDeps.detector_tier1` and `detector_tier1_output` (`dev_rag.py:83,88`, used at `rag_stages.py:131-134`) are built by the same factory, so they also scan with the request's program.
- **No silent fail-open after `request_org` goes.** A detector not built by the factory would scan with no customer program. So:
  - `bind_surface` refuses a detector that carries no program;
  - a parametrised test drives an org-B rule through all 9 `bind_surface` sites, moderations and both RAG tier-1 detectors, and asserts org B's program fires at each.

### 5.2 Customer regex: exact `re` meaning, pre-emptible matching, bounded cost

**Requirements.**
1. **Exact semantics:** a customer pattern matches exactly what Python `re` matches today, with no bypass through Unicode digits, separators, combining marks or case folding.
2. **Pre-emption:** a catastrophic pattern is stopped within a bound.
3. **Event-loop bound:** no tenant takes more than a stated share of a worker's event loop.
4. **No false refusals:** a streamed response is refused for regex cost only where the same text, scanned once as a non-streaming response, would be refused. Long streams, slow upstreams and many ordinary rules are not refused.
5. **Bounded translation and compile cost,** kept off the projector's safety path.
6. **Tenant isolation:** one tenant's rules cannot starve another tenant's scans, on the loop or off it.

**Why the obvious engines fail (measured by the reviewers and re-measured here).**
- **RE2** has ASCII-only `\d`, `\w`, `\s` and `\b`.
- **`regex` in VERSION0** differs from `re`:
  - `\s` misses U+001C–U+001F;
  - `\w` differs on about 25k code points;
  - `\b` differs next to combining marks;
  - case folding differs (`re` folds i/ı/İ and k/K/K together);
  - it reads fuzzy `{e<=1}` and POSIX `[[:alpha:]]` syntax where `re` reads literals.
- **`re`** cannot be pre-empted.

The revision-5 prototype and its evidence are in `docs/plans/evidence/2026-11-11-f1a1-regex/`; `RESULTS.md` indexes them and `perf5.md` holds every cost number below. All times are thread CPU time on this host (2 shared CPUs), Python 3.12.3, `regex` 2023.8.8.

#### 5.2.1 The translator

- **Parsing.** The pattern is parsed by `re`'s own parser (`re._parser`). Escapes, sets, verbose mode, scoped flags and quantifier syntax therefore mean what `re` says.
  - The tree is re-emitted for `regex`, and no global flag reaches `regex`.
  - `.`, `^` and `$` get explicit scoped flags.
  - Braces and brackets are emitted only as `re` parsed them, so fuzzy and POSIX syntax cannot appear.
  - **The emitted vocabulary is fixed:** literals, escapes, sets, `\p{…}` classes, groups, alternation, counted, lazy and possessive repeats, atomic groups, lookarounds, `\A`, `\Z`, scoped flags, backreferences and conditionals. Fuzzy matching, recursion, `(?r)` reverse search and `(*SKIP)`/`(*FAIL)` verbs are never emitted, so the `regex` bugs confined to them (#525, #607, #608) are out of reach.
- **Categories.** `\w` becomes `[\p{L}\p{N}_]`, `\d` becomes `\p{Nd}`, and `\s` becomes an explicit 10-range set taken from `re`. Negations follow the same pattern. They are never case-folded, because `re` matches a category by the character itself. Inside sets, the categories are split out of the set.
- **Word boundaries (Reviewers A-B2, B-B1).** `\b` and `\B` are lookarounds over that `\w`, and `\B` is false on the empty string, as in `re`.
  - When the neighbouring item can only start (or end) with a word character, or only with a non-word character, one side of the boundary is already known, and the translator emits **one** lookaround for the other side: `\b[A-Z]` becomes `(?<![\p{L}\p{N}_])[A-Z]`.
  - This is exact. If the next item must consume a word character at position p, then `\b` at p holds exactly when the character before p is not a word character. The neighbour's class comes from `re`'s own verdict, case variants included, through groups, alternations and repeats with a minimum of at least 1. Anything else keeps the two-sided form: a nullable neighbour, `.`, a negated set, or a mixed class such as `(?i)ι`, whose variant U+0345 is not a word character.
  - It removes the slowdown the reviewers measured. The IBAN-shaped `\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b` now takes 0.03 ms on 32 KiB of prose and 0.23 ms on code, against 0.48 and 0.58 ms for `re` (two-sided: 7.2–20.7 ms).
- **Atomic repeats, where exact.** A greedy repeat of one character class X is emitted atomically when the next item must start with a character of the other word class: giving back an X character leaves an X character next, where that item cannot start. Before `\b` this also needs at least one X to remain, because `\b` compares both sides (the fuzz found `1{,2}\b` while this rule was being built).
- **Possessive repeats (Reviewer A-S1).** `re` backtracks neither into a possessive repeat nor into any one of its iterations; `regex`'s `X{m,n}+` backtracks inside an iteration. Possessive repeats are emitted as `(?>(?>X){m,n})`.
- **Case-insensitive literals and sets.** These use `regex`'s native case folding. The translator then takes `re`'s verdict on every one of the 2,927 cased code points, and adds or removes the code points where the two engines disagree. That keeps `re`'s special folds exact.
  - **Cost (Reviewers A-B1, B-B3).** The verdict is **one** `findall` over a string of all cased code points, by `re` and by `regex`, instead of 2,927 calls each. Translation of the worst legal pattern (64 distinct case-insensitive two-character sets) takes 9.3 ms.
  - The cased-code-point table is generated at build time (`gen_cased.py`, with a CI `--check`), not at import. Import now takes 0.02 s, against 1.16 s before (Reviewer B, NIT 8).
- **Two `regex` bugs, worked around** (both present in 2023.8.8 and 2026.9.29):
  - **The first-character prefilter.** When a pattern starts with an optional case-insensitive item, `regex` builds a prefilter that folds the following case-sensitive set; measured, `(?i:a)?[^\p{L}]` misses U+0345. Patterns containing a case-insensitive part get a leading `(?=[\s\S]|\Z)`, which disables that prefilter.
  - **Lone negated characters in an alternation (new in revision 5).** `regex` merges alternatives that are single negated characters into one negated set, so `(?:[^9]|[^c])` matches only characters that are neither 9 nor c, instead of every character. It under-matches, which would be a bypass. The translator emits a negated single character with its member repeated (`[^\x39\x39]`), which `regex` keeps as a set. Without the workaround, the extended fuzz finds 10 mismatches in 2,000 patterns. With it, the exhaustive single-character-alternation test below finds none.
- **Refused at translation:** the ASCII and LOCALE flags, a backreference under IGNORECASE (`regex` compares backreferences with its own case folding), and anything over the limits in §5.2.3. Every refusal is decided on the parse tree, so the parse-only check refuses exactly what translation refuses.
- **A fail-open at HEAD, removed (Reviewer A, NIT 3).** On 3.12.3, `re` raises `SystemError` for some possessive patterns with a capture group. `conditions.py:_work` catches only `re.error`, so the rule silently matches nothing. The gateway no longer compiles customer patterns with `re`. Control's validation treats any exception from `re` as a refusal, and a regression test pins both behaviours.
- **Evidence (exactness):**

  | Check | Result |
  |---|---|
  | `\d \D \w \W \s \S`: in and out of sets, negated, with and without `(?i)`, on every code point | 0 mismatches |
  | `\b \B \bx x\b \Bx x\B` (two-sided) next to every code point | 0 mismatches |
  | One-sided boundaries: 30 patterns × 2 flag sets, with each neighbour class interleaved with every code point, plus the text edges | 0 mismatches |
  | Every case-insensitive single literal (2,927) against every code point (Reviewer A's test) | 0 mismatches |
  | Every pair and triple from a 19-item vocabulary of single-character items, in an alternation: plain, repeated, in a negative lookahead and after a literal, under no flag, `(?i)` and `(?s)` | 0 mismatches in 86,640 patterns |
  | Differential fuzz against `re`. The grammar adds possessive repeats, atomic groups, lookbehinds and single-character alternations. Patterns run in worker processes, so a pattern that hangs inside `re` itself is skipped and counted | 0 mismatches: 9 seeds × 6,000 patterns, 401,408 pattern-text pairs. 60 patterns hung inside `re` and were skipped; 21 hit `regex`'s 1 s timeout where `re` also took 0.7–1.7 s |

  Match speed against `re` (32 KiB of prose and of code, 15 patterns including the `\b`+class family): the worst ratio is **3.7×** (`\b\w+\s+(?:token|secret)\b`, 4.3 against 1.2 ms), and 11 of the 15 patterns are faster than `re`.

#### 5.2.2 The `regex` pin and its supply chain

- **The Unicode database must match.** `regex` is pinned to **2023.8.8**, whose Unicode database (15.0) equals Python 3.12's. On that pair, `[\p{L}\p{N}_]` equals `re`'s `\w` on all 1,114,112 code points, and `\p{Nd}` equals `\d`.
  - Later releases differ (measured on Python 3.12):

    | `regex` | `\w` differs on | `\d` differs on |
    |---|---|---|
    | 2023.10.3 to 2024.5.15 | 622 | 0 |
    | 2024.11.6 | 5,004 | 80 |
    | 2026.9.29 | 22,484 | 90 |

  - `RESULTS.md`'s revision-4 claim that all releases from 2024.11.6 to 2026.9.29 differ on 5,004 code points was wrong; it is corrected (Reviewer B).
- **The build (Reviewers A-S2, B-4).** PyPI has no cp312 wheel for 2023.8.8, and the runtime image (`python:3.12-slim`) has no compiler.
  - A builder stage compiles the wheel from the sdist, fetched under `--require-hashes`, for linux/amd64 and linux/arm64. CI caches the two wheels and records their sha256.
  - The runtime stage installs them with `--no-index --require-hashes`. No compiler reaches the runtime image.
  - 2023.8.8 predates Python 3.12, so the exhaustive and fuzz CI jobs (§5.8) are the only check of this combination. They run on every PR that touches the translator, the pin or the image.
- **Where the pin lives (Reviewer A, NIT 1).** `regex==2023.8.8` is a base dependency in `gateway_v2/pyproject.toml`, and `types-regex` is a dev dependency for `mypy --strict`. `requires-python` becomes `==3.12.*`, matching the boot check.
- **Boot check.** The gateway refuses to start unless all of these hold:
  - `importlib.metadata.version("regex") == "2023.8.8"` (`regex.__version__` reports `2.5.132` on this release, so it cannot be used);
  - `unicodedata.unidata_version == "15.0.0"`;
  - a full functional comparison: `[\p{L}\p{N}_]`, `\p{Nd}` and the `\s` set against `re`'s `\w`, `\d` and `\s`, by `findall` over every code point. That costs 0.27 s of CPU per process at boot.
- **Upstream fixes after 2023.8.8.** Later releases fixed memory-safety bugs in compile and scan code that translated patterns can reach:
  - #611 (a heap out-of-bounds write at compile time) and #612 (a size underflow), both in 2026.8.30;
  - a segfault fix in 2026.3.32;
  - allocation and error-propagation fixes in PRs 615–618.

  Whether #611 and #612 exist in 2023.8.8 is not confirmed. Therefore:
  - **Crash fuzz in CI.** Translator output from the fuzz grammar, plus the limit-edge shapes, is compiled and matched in child processes. A child that dies by a signal fails the job, and the pattern is recorded.
  - **ASAN corpus.** An ASAN build of the wheel runs the same corpus weekly.
  - Only patterns that pass the publish limits reach `regex`, which keeps the reachable surface to the shapes the fuzz covers.
- **The pin is frozen, not just old.** It cannot follow upstream without losing exact `\w`.
  - **Exit plan:** explicit code-point sets built from `re` (749 ranges for `\w`, 64 for `\d`). They are exact on any `regex` release (0 mismatches on 2026.11.9 and on 2023.8.8, measured by Reviewer B), but match 6–14× slower.
  - If a security fix must come from a later release, the translator switches to explicit sets. The per-pass budget absorbs the slowdown, and the boot check and CI switch with it. §9 records this.

#### 5.2.3 Publish-time limits and where they are checked

- **The limits.** Revision 5 adds the last three rows:

  | Limit | Value | Why |
  |---|---|---|
  | Pattern length | ≤ 1000 | today's limit |
  | Each counted repetition, and the product of nested counted repetitions | ≤ 1000 | revision 4 |
  | Regex rules per plan | ≤ 64 (`_MAX_REGEX_RULES_PER_PLAN`) | revision 4 |
  | **Total expanded repetition:** the sum, over counted repeats, of the count × the product of the enclosing counts | ≤ 1000 per pattern | Reviewer B: `"x{1000}"*140` passed every revision-4 limit, then compiled in 92 ms and added 36 MiB |
  | **Distinct case-insensitive items** (each costs one verdict) | ≤ 64 per pattern | Reviewers A and B: 990 distinct items took 1.67–2.2 s to translate |
  | **Distinct case-insensitive items per plan** | ≤ 512 | 64 rules × 64 items took 1.39 s of CPU to translate and compile |

- **One parse-only check, in three places.** `check_limits` parses with `re._parser` and walks the tree for the refusal rules and the limits. It never translates and never imports `regex`. It is used by:
  - control's serializer, which refuses at save (as a byte-identical copy in `ai_mesh_shared/regex_limits.py`; Django admin and `load_policies` are covered by the projector check);
  - the projector's `validate_plan`;
  - the gateway, before translating.

  A CI test asserts that the two copies are identical and that the three callers agree on the fuzz corpus.
- **Measured cost at the limits (thread CPU):**

  | What | Cost |
  |---|---|
  | Parse-only check, worst single pattern (a 990-character alternation) | 1.2 ms |
  | Parse-only check, hostile 64-rule plan | 16.5 ms |
  | Translation, worst pattern (64 distinct case-insensitive sets) | 9.3 ms (compile 4.7 ms) |
  | Compile, worst shape at expanded repetition 1000 (`(?:\b\w{3}\b ){250}`) | 0.8 ms; 1.3 MiB traced at peak |
  | Compile, a 990-character literal alternation | 4.0 ms |
  | Translation + compile, a plan at every per-plan limit (512 distinct case-insensitive items, plus 56 compile-heavy rules) | 236 ms |

- **The projector's safety path (Reviewers A-B1, B-B3).** Today `desired_state` runs `validate_plan` before `mark_projected` writes the safety heartbeat (`projector.py:209,224`). Revision 5 changes three things:
  - The projector runs only the parse-only check, never translation or `regex`: 16.5 ms for a hostile 64-rule plan, against the 16 s lag bound.
  - Validation is memoised by body hash, and it moves out of `desired_state` into `_plans`, with a per-round time budget of a quarter of `lag_ms`. A plan not validated within the budget keeps its previous record and is validated in the next round, with a note. A burst of new plan bodies therefore delays only those plans, never the heartbeat.
  - Test: a hostile 64-rule plan, re-published with a new body every round, keeps the heartbeat inside `lag_ms`.
- **Breaking a limit:** control refuses at save. An existing stored rule over a limit becomes a refused rule, which fails closed with `policy_regex_timeout` on an applicable request, as today. It is counted in the projector's `refused_conditions`.
- **Gateway compile.**
  - Translation and compile are lazy: they run on the first request that needs the org's program, on the scan scheduler (§5.2.5), off the loop, and charged to that org.
  - They are **single-flight** per `(org, plan version)`, so concurrent first requests wait on one compile.
  - Each rule is its own step, with a GIL yield between rules. Measured while a worker compiled the per-plan-limit plan: without the yield, back-to-back `regex.compile` calls starved the event loop for 90 ms; with it, the worst loop stall was 7.3 ms, the same as any busy pure-Python thread (7.6 ms).
  - The per-worker program cache is bounded by **bytes**: each entry is charged its compile footprint (≤ 1.3 MiB per pattern at the limits), against a contract-derived budget, with an eviction metric.
- **Other `re` call sites.**
  - `rewrite_template.py:91-98` (compile-time scan): goes through the same engine. Its literal `_CHECK_BUDGET_S = 0.25` is replaced by `guard_wait_budget_s()`.
  - Control's `_matches_own_pattern` (`firewall_v2_publish.py:586-593`): gated by the parse-only check and `_has_redos_shape` first, and run off the request thread with a bound.
- **What does not change.** `_redos_shape` keeps refusing the shapes it refuses today, and the timeout posture is unchanged.

#### 5.2.4 Matching, budgets and the streaming scan

- **The budget unit is thread CPU time.** `regex`'s timeout counts the matching thread's CPU time across a whole `finditer`, not wall time.
  - Measured: a 100 ms timeout fired at 100 ms of thread CPU, idle, and at 540–704 ms of wall time with three busy loops sharing the 2 CPUs.
  - Revision 4's "overshoot" was this host contention, not the engine. Every budget, bucket and bound below is therefore in thread CPU time, measured with `time.thread_time()` around each call. Wall time under host oversubscription is a capacity matter (GW34), not a tenant-isolation one.
- **Calls.** `compiled.finditer(text, pos=q, timeout=t, concurrent=True)`, which releases the GIL. The thread created per match is removed. When the computed `t` is ≤ 0, the scan refuses without calling `regex` (a negative timeout means *no* timeout in `regex`).
- **The per-pass budget scales with the text.** An off-loop pass gets `guard_wait_budget_s() × max(1, len(text) / 128 KiB)`, shared by all of its rules: 100 ms up to 128 KiB, and 400 ms at the 512 KiB output ceiling.
  - A fixed budget refused long texts that `re` scans today. A linear scan costs time in proportion to the text, so the budget does too, and catastrophic patterns still hit it.
  - Non-streaming scans use the same formula. A timeout refuses the request with `policy_regex_timeout`, as today.
- **The streaming scan is incremental (Reviewers A-B2, B-B1).** Today every delta rescans the whole output (`egress/stream.py:548`), so a delta costs O(output) and a stream O(n²). Revision 5 keeps each rule's hit list **identical** to that rescan while scanning far less.
  - **The idea.** An attempt at position t whose outcome can no longer change is *final*. After each scan, the scanner keeps a resume point q before which every match and every failed attempt is final, and the next scan resumes at q with `finditer(text, pos=q)`.
    - `pos` keeps the earlier text visible to lookbehinds, and `^`/`\A` do not match at q, exactly as in a whole-text scan.
  - **Two exact finality rules,** from the parse tree:
    - **Reach.** `analyze` returns the rule's read reach R: how far right of its start an attempt can read, with lookaheads, `$` and boundaries included. It is finite unless the pattern has `*`, `+`, `{n,}`, a backreference, or a lookbehind that reads to its right. An attempt at t is final once t + R ≤ len(text).
    - **Stoppers.** The translator also builds the class of characters that no consuming item of the pattern can match. An attempt cannot consume a stopper, and every assertion it evaluates sits at or before the first stopper at or after its start. So the attempt at t is final once any stopper follows t. This makes unbounded rules incremental too: `\b\w+\s+(?:token|secret)\b` stops at any punctuation. Patterns with `\Z` or a non-multiline `$`, which read the end of the text, have no stoppers.
  - **Fallback.** A rule that can match empty, or that has neither a finite reach nor a stopper (`(?s)a.*b`), keeps the whole-text rescan.
  - **State.** Final hits that end before the released offset are dropped, so the state per stream stays O(R + held text).
  - **Evidence.** `incremental_fuzz.py` compares the incremental hit list with a whole-text `finditer` after every delta of random chunkings, for fuzz-grammar patterns and for real DLP shapes: 0 mismatches over 39,998 incremental streams (83% of 48,000 fuzz patterns) and 655,969 deltas. With reach alone, 55% of the revision-4 fuzz grammar's patterns were incremental; with stoppers, 83% of the revision-5 grammar's are. Building it, the fuzz also found two reach-analysis bugs (a `0 × ∞` in an optional unbounded group, and a lookbehind containing a lookahead), both fixed before these runs.
- **The streaming budget.** Each delta's inline customer scan gets one budget of **half a scan window** of thread CPU (5 ms at the default p99), shared by all its rules.
  - **No refusal inline.** When a rule's inline call times out, or the inline budget is spent, the rest of that delta's customer scan **moves off the loop** as one pass, under the per-pass budget. The stream awaits that pass before releasing those bytes. A whole-text-rescan rule that moved off the loop stays off the loop for the rest of that stream.
  - **Coalescing.** A stream has at most one off-loop pass in flight; deltas that arrive meanwhile are scanned together by the next pass. The released bytes do not depend on how deltas are grouped (chunk-split invariance, Property 4), and the existing property test runs over the coalescing layer.
  - So a stream is refused for regex cost only when one pass over its text exceeds the per-pass budget: the same rule as a non-streaming response of the same text (requirement 4).
- **Measured per-delta cost** (10 rules, thread CPU, per 4-character delta at the given output length). Rule set B is Reviewer B's (IBAN shape included); rule set A is Reviewer A's (`\b\w+\s+(?:token|secret)\b` included). "Unpunctuated" is words and spaces only: no stopper ever appears, which is the worst case for stopper-mode rules.

  | Rules | Text | 32 KiB: rev. 4 → rev. 5 | 128 KiB | 512 KiB |
  |---|---|---|---|---|
  | B | prose | 0.71 → 0.04 ms | 2.50 → 0.04 ms | 11.1 → 0.04 ms |
  | B | code | 1.29 → 0.04 ms | 5.04 → 0.04 ms | 20.2 → 0.04 ms |
  | B | unpunctuated | 0.73 → 0.07 ms | 2.79 → 0.18 ms | 11.3 → 0.57 ms |
  | A | prose | 4.83 → 0.11 ms | 37.5 → 0.06 ms | 88.3 → 0.04 ms |
  | A | code | 5.35 → 0.04 ms | 19.5 → 0.04 ms | 80.5 → 0.04 ms |
  | A | unpunctuated | 6.40 → 4.30 ms | 21.0 → 18.7 ms | 85.3 → 78.7 ms |

  - **The worst case is visible.** Set A on unpunctuated text: the token/secret rule finds no stopper and rescans. It crosses the 5 ms inline budget at about 32 KiB, moves off the loop and coalesces. A pass at the 512 KiB ceiling takes about 79 ms, against a 400 ms per-pass budget, so it is not refused. Its cost lands on its own org's scheduler share, which slows that stream instead of the loop.

#### 5.2.5 Tenant isolation: the inline bucket and the scan scheduler

- **Inline share (Reviewers A-S4, B-B2).** Each org has a per-worker **inline loop bucket** of thread CPU, with rate ρ = 25% of one core and a burst of one scan window, both contract-derived.
  - Every inline customer call is debited its measured thread CPU.
  - When an org's bucket is empty, that org's scans go off the loop. They are not refused.
  - So on one worker, an org's inline customer-regex time is at most ρ of the loop plus one burst, however many streams it runs. Revision 4's "admitted request rate × one scan window" bound was wrong: a rule just under the cap costs up to one window per delta, not per request.
- **Off-loop passes run on a scan scheduler,** not directly on the shared executor. Reviewer B measured tenant A's pathological requests pushing tenant B's off-loop scans to p99 1.4–2.1 s on the shared executor.
  - The scheduler has T scan threads, contract-derived from the core count.
  - Each org has its own FIFO, served by deficit round robin weighted by each pass's measured thread CPU, so under contention every active org gets an equal CPU share.
  - An org uses at most max(1, T − 1) threads at a time, so one org can never hold every thread.
  - The queue depth is bounded by admission: each admitted request has at most one pass queued or running (streams coalesce). So the scheduler adds no new shed path, and admission's existing concurrency cap bounds it.
  - **Bound.** A tenant-B pass waits for at most one running pass per thread (each within its per-pass budget of CPU), then is served within one DRR round. With fewer than T orgs active, B always finds a free thread.
- **Every other customer scan goes through the scheduler too:**
  - chat input (`openai_chat.py:300`);
  - the non-streaming whole-response scan (`openai_nonstream.py:176-177`);
  - MCP input and replies (`mcp.py` `_forward`);
  - embeddings (`openai_misc.py:445`);
  - moderations, RAG and the error frame;
  - **STRICT_WITHHOLD (Reviewer A-S3).** `build_output_detectors` returns `runner=None` when a plan has no built-in output families (`output_detectors.py:162-163`), so `strict.py:439-440` scans the whole withheld output on the loop. The scheduler is now always passed as the runner;
  - gateway compile (§5.2.3), charged to the org.

### 5.3 Resolvers and base detectors

- `phase_resolvers = phase_resolver_factory()` in production, so plan rules are enforced and RAG stages work.
- `input_detector`, `error_scanner` and `output_detector` come from the factory. The base detectors are the GW09 deterministic set.

### 5.4 Audit

1. **Build order.** `_build_admission` moves above `build_wiring(runtime)` (`production_profile.py:493-496`). It depends only on contract, env, clock and `q_safe`, and it supplies `bounds.audit_depth`.
2. **Sink.** `AuditSink(ValkeyAuditStore(runtime.store_client, runtime.store_keys), budget, queue_depth=bounds.audit_depth)`. `build_incident(..., audit=sink.emit)` makes the sink the tap's downstream. `deps["audit"]` stays the `IncidentAuditTap`, because the risk tracker needs an `AttemptSink`. `DevAudit`'s tested pieces are promoted to the production module rather than duplicated.
3. **The budget's tenant count (Reviewer 4 B2, Reviewer A B4).**
   - `StreamBudget`'s `tenants` is the cardinality of the signed PLAN index (`StoreKeys.index(StateKind.PLAN)`, one `ZCARD`). It is read by the sampler owner each tick and shared, never taken from a worker's local plan cache.
   - The count over-counts in the safe direction:
     - the PLAN index keeps offboarded orgs (no `ZREM`);
     - `_unauthenticated` has a stream but no PLAN entry, so 1 is added.
   - **Unknown budget:** in production it is refused, not defaulted.
     - The per-org `stream_maxlen` fallback is the RC2 configuration that filled a 10.4 GiB store in 51 minutes (`storemem.py:123-131`).
     - `gateway-v2-prod` therefore requires `AMF_AUDIT_STORE_BUDGET_MB`, or a store `maxmemory`. Without one, readiness fails with the reason.
4. **Sampler owner.** Production workers have no index, so ownership is a Valkey lease.
   - **Keys:** two new `StoreKeys` entries **outside** the per-org audit namespace, so no org slug can collide with them (an org named `sampler` could, under `{ns}:audit:`):
     - `{ns}:auditctl:sampler`, the lease;
     - `{ns}:auditctl:decision`, the published `BudgetDecision` with its timestamp.
   - **Lease:** taken with `SET … NX PX <ttl>`. It is renewed by compare-and-expire, a Lua script that runs `GET == me` then `PEXPIRE`, so only the holder renews. The TTL is contract-derived from the sampler period.
   - **Decision:** the holder runs `MemorySampler` and publishes the decision. Every worker adopts it, unless it is older than two sampler periods; then the previous decision stays in force and a staleness gauge is raised.
   - Losing the lease stops sampling; any worker can take it over.
   - Every worker still runs `startup_check`, as today.
   - **Reserved slugs (Reviewer B, revision 3 NIT 11).** The audit ids `_calibration` and `_unauthenticated` are also valid org slugs today, the same class of collision as the lease key. Control refuses org slugs that start with `_`. A data check in the migration lists any existing one, and blocks the migration until it is renamed.
5. **Lifecycle.**
   - An `AuditLifespan` hook starts the writer and the sampler task.
   - **At shutdown,** the writer is asked to stop after its current batch, not cancelled mid-write. Cancelling mid-write lost the in-flight batch, which was counted as produced but not written (measured: 300 produced, 0 written). `drain(timeout)` then writes what remains, before the runtime closes the client. `StateServedApp` already closes the client after the hooks.
   - **The drain gets time to run:**
     - uvicorn gets a contract-derived `timeout_graceful_shutdown`;
     - the drain hook is bound in the multi-process path too, which today does not bind it.
   - **Readiness reflects the writer:**
     - a hook's start failure fails readiness (`StateServedApp._run_hooks` records it; today it is only logged, `state_runtime.py:475-481`);
     - the writer task's done-callback marks readiness unhealthy if the task ever ends while serving.
   - The one-tuple unpack in `test_lgw29_production_wiring` changes, because there are now two hooks.
6. **Metrics (Reviewer 4 M4).** The gateway publishes **producer and bound** series only: queue depth, drops, written-by-org count and budget bytes. The durability series (`completeness_ratio`, `records_lost`, `store_memory_reachable`) belong to audit_control and are not published by the gateway.
7. **Record attribution.** Each audit record carries the worker id it already uses (`host:pid`) in its detail. No index is invented.

### 5.5 GW35, response store, N14

- `unauth_audit = build_unauth_gate(contract, clock)`, and Publisher `unauth=UnauthAuditCounters(gate)` (+2 series).
- **Control side (Reviewer 4 M5).** Ingest drops `_unauthenticated` rows when there is no org map (`v2_audit_ingest.py:110`), so the invalid-key test would have no console row. F1a-1 states the reconciliation: unauthenticated rows are counted by the gateway series and land in the console only for orgs that have an alias mapping. The live test asserts the counter, not a row.
- `response_store = ValkeyResponseStore(runtime.store_client, ttl_s=contract.response_store_ttl_s(), keys=runtime.store_keys)`.
- **N14.** `Publisher` takes `rehydration: FlatProducer | None`.
  - `_rehydration_families` returns `([], 0)` for `None`, following the existing `_unauth_families` pattern.
  - `series_count` drops the 44-series term.
  - The gateway passes `None`.

### 5.6 Process hardening (per worker, Reviewer B 7)

- `RLIMIT_CORE=0` and `prctl(PR_SET_DUMPABLE, 0)` are applied **in each worker's app factory** (`serve.serving_app`). A setting made in the parent does not survive uvicorn's spawn, which was measured.
- The test checks `PR_GET_DUMPABLE == 0` from inside a spawned worker.
- **The parent too (Reviewer B, revision 3 NIT 9).** `main()` builds the production dependencies in the parent before spawning workers (`serve.py:700-720`), so the same hardening runs at the top of `main()`. The runbook notes that `dumpable=0` also blocks same-uid `py-spy` and `gdb`.
- The projector hardening and `_FILE` secrets belong to F1a-3, where the projector starts holding provider keys.

### 5.7 Boot log

- Each line states what is real.
- `apply_production` replaces the `input_detector`, `provider` and `catalog` survivors with true lines:
  - provider: still a stub, declared;
  - detectors: production factory;
  - audit, response store and unauth audit: their real wiring.
- The stale header goes, and the GW34 `tool_bounds` line stays.
- A test pins the full production line set.

### 5.8 F1a-1 tests

- **Unit and property tests:**
  - **N5:** an org-B rule fires with org B's program at every scan site, not only the four off-loop ones. The sites are all 9 `bind_surface` calls, moderations and both RAG tier-1 detectors. The off-loop ones fail on HEAD. `bind_surface` refuses a detector with no program.
  - **Pinning:** a mid-request plan update does not change the program a request uses.
  - **Semantics against `re`** (all in CI, on the pinned pair):
    - the exhaustive per-code-point class and boundary tests, including the one-sided boundaries (`exhaustive_boundary.py`), every case-insensitive single literal, and every pair and triple of single-character items in an alternation (`alt_exhaustive.py`, which pins the lone-negated-character workaround);
    - the differential fuzz, whose grammar includes possessive repeats, atomic groups, lookbehinds and single-character alternations. It runs in worker processes with a per-pattern timeout, so a pattern that hangs inside `re` is skipped and counted; a `regex` timeout where `re` finished is counted apart;
    - the incremental streaming fuzz: after every delta, the incremental hit list equals a whole-text `finditer`, for both finality rules (reach and stoppers);
    - the reviewers' bypass cases: U+001F separators in a card pattern, U+001C in `password\s*=`, a combining mark after `\bpassword\b`, `(?i)\bssn\b` against `ssǹ`, `(?:a+x?){2,}+` on `aaa`, and `(?:[^9]|[^c])`;
    - the `SystemError` regression: `x(?:(a)a{1,2}|aba(?i:A)|x?x?){2,}+` is refused at publish and fails closed at the gateway. It never silently matches nothing;
    - the boot check, including the full functional comparison.
  - **Performance gate.** On a fixed corpus (prose and code-like text, 32 KiB, including the `\b`+class family), each translated rule's thread CPU stays within 6× `re`'s, with each side floored at 0.05 ms. Revision 5's worst measured ratio is 3.7×; the gate is set above it so that runner noise does not flake, and it still catches a regression like revision 4's 11–30× `\b`.
  - **Crash fuzz.** Translator output is compiled and matched in child processes. Any death by a signal fails the job.
  - **Regex DoS:**
    - patterns that pass `_redos_shape` and the limits, yet time out under `regex`. Example: `(\w+\s?){1,30}$`;
    - **two-tenant tests,** each asserting tenant B's per-delta loop delay and off-loop wait against the §5.2.5 bounds, in thread CPU:
      - (a) tenant A has R rules, each tuned just under the inline budget, on the non-strict streaming path, with **C concurrent streams that are never refused**. A's inline share stays within ρ plus one burst;
      - (b) tenant A has C concurrent pathological streams. Each moves off the loop, then is refused after one per-pass budget;
      - (c) the same as (b), on the chat input path and on STRICT_WITHHOLD;
      - (d) tenant A floods the scan scheduler with pathological non-streaming requests at the admission limit. Tenant B's off-loop pass waits at most one running pass per thread;
    - negative and zero remaining time never reaches `regex`.
  - **No false refusals:**
    - a 32k-token stream of prose and a 16 KiB code-like stream complete, each with 10 ordinary rules including the IBAN shape and `\b\w+\s+(?:token|secret)\b`;
    - a stream at the 512 KiB output ceiling with Reviewer A's 10 rules completes, on unpunctuated text too (its token/secret rule moves off the loop);
    - a stream whose whole-text-rescan rule outgrows the inline budget moves off the loop and completes, releasing the same bytes as a non-coalesced run;
    - the per-pass budget scales with the text: a pass over 512 KiB gets four base budgets;
    - a request whose first token arrives after 2 s still runs its output rules.
  - **Limits:**
    - refusals for total expanded repetition, distinct case-insensitive items per pattern and per plan, in control, the projector and the gateway, with one shared verdict;
    - a hostile 64-rule plan, re-published with a new body every round, keeps the projector heartbeat inside `lag_ms`;
    - event-loop lag while a worker compiles a plan at every per-plan limit stays within the measured bound (one rule per step, GIL yield between rules);
    - single-flight compile: 50 concurrent first requests trigger one compile;
    - the compiled-program cache stays within its byte budget;
    - `_matches_own_pattern` cannot hang a control worker.
  - **Audit:**
    - sink lifecycle: start, then stop-after-batch and drain at shutdown, with no batch lost;
    - a hook start failure fails `/readyz`, and so does a writer task that dies;
    - a lease takeover between two fake workers, and compare-and-expire renewal;
    - an org slugged `sampler` writes normally;
    - a stale decision is not adopted;
    - the tenant count comes from the PLAN index, +1;
    - an unknown budget in production fails readiness.
  - **Production resolvers:** with a fake provider, an **output** plan rule is enforced by the production `phase_resolvers`, and RAG stages no longer refuse.
  - **Metrics:** the Publisher series count without the re-hydrator family; the gateway publishes no durability series.
  - **Response store:** a cross-worker `/v1/responses` round trip, with two app instances on one store.
  - **Hardening:** `PR_GET_DUMPABLE` inside a spawned worker.
  - **Boot log:** the full production line set.
- **Live production boot** (postgres, redis, control, projector, re-hydrator, gateway in production, **one worker**):
  1. A BLOCK prompt gives its declared refusal, zero provider calls, and an `EnforcementEvent` row matched by nonce.
  2. An allowed prompt gives the stub's declared error and no fail-open pass. The console row's stage shows `error`, not `block`.
  3. An invalid key gives 401, and `amf_audit_unauthenticated_total` increments.
  4. `amf_audit_budget_bytes > 0`, and `XLEN ≤ maxlen` for the org stream.
  5. Δproduced = Δwritten + dropped + failed, measured outside shutdown.
  6. `/metrics` series count equals `SERIES_COUNT`, with `AMF_FD_LIMIT` stated.
  7. Valkey operations per request are recorded as the F1d baseline.

  Cross-worker `/v1/responses` is covered by the unit test, because nothing can be stored behind the empty stub.
- **Regression:** full gateway_v2 suite, control suites and CI.

## 6. F1a-2 requirements: MODEL state (binding; design and review before code)

1. **Forward-compatible stamp, as its own release.**
   - The stamp verifies over the raw cursor map, ignores unknown kinds, and requires only the kinds this build applies.
   - The re-hydrator's held-stamp comparison also ignores missing kinds.
   - The re-hydrator names MODEL, and the projector emits it, only behind explicit flags. This follows the `project_mcp_key_policy` precedent (`domain/state_knobs.py:199`).
   - **The rollout floor is enforced, not only documented.**
     - Every gateway exports its build's state generation.
     - The re-hydrator and projector flags are declarations checked against the fleet's minimum, as GW29 does with `AMF_GATEWAY_MIN_SCOPE_GENERATION`.
     - Every gateway image that could be deployed, rollbacks included, runs step 1 before any step-2 re-hydrator starts.
     - Rolling back a re-hydrator after step-2 gateways are deployed is forbidden, and is refused by that check.
   - Tests cover a mixed-build re-hydrator pair and stamp time never moving backwards.
2. **MODEL applier contract.**
   - Signature and index failures are round-fatal and raise an alert.
   - Body failures (decode, unknown provider or field, decrypt, unknown kid) are caught per record. They set an org-level `unavailable(version)` marker and the cursor advances.
   - An unknown provider or field omits only that entry, and is counted.
   - Readiness never depends on one org's record.
   - Rollback refusal applies per warm org. A cold fetch reads only the manifest and the record (`state_runtime.py:299-318`); the index score is not signed. The F1a-2 design must close that gap or state the bound it accepts.
   - `StateWriter.rollback` refuses MODEL.
3. **Request path O(1) in orgs.**
   - `serves_alias(org, alias)`, `preflight` and `_plan` read a per-org `(org, casefolded alias)` dict.
   - Client and validation checks run per org entry when the entry is applied.
   - `all_candidates()` stays off both the request path and the apply path.
   - A test shows the cost does not change between 1k and 100k orgs.
4. **Bounded caches.**
   - The MODEL view is a contract-derived LRU with an eviction metric and KEY's `_touched` race retry. Holding only `{kid, nonce, ct}` and decrypting per request (0.8 µs) is to be evaluated, so plaintext is not resident for every org a worker has served.
5. **Absence.**
   - Every active org has a record, possibly an empty one. An empty list goes through the existing `no_provider_configured` seam (`catalogue.py:54`).
   - Offboarding writes a tombstone.
   - An omitted row is carried as `{alias, omitted: reason}`, so the gateway can answer with a declared code instead of `model_not_found`.
6. **Projector cost and safety.**
   - Changes are detected by a projector-private memo per `row_id`: a digest of the Fernet token, the AAD tuple and the published `{kid, nonce, ct}`. A row is decrypted only when it changes.
   - Plan validation is memoised by body hash (and is parse-only and time-boxed, §5.2.3).
   - The omission notes, logged one per note (`projector.py:214-216`), never carry `base_url` (Reviewer B, revision 3 NIT 10).
   - Removals and real per-row key changes go before the heartbeat. Kid re-wraps are a bounded backlog after it.
   - **Breaker:** when the omission ratio for one reason crosses a threshold, the previous records are kept and an alarm is raised. This prevents one wrong control key from emptying every catalogue.
   - A mixed console edit (one model removed, one added) is written as two versions.
   - A round-time test runs at 100k orgs × 5 models, with a budget well under `lag_ms`.
7. **Source columns.**
   - `core_llmmodelconfig` has no `weight`, `api_version` or `mode` column, and `unique_together(organization, model_name)` allows one candidate per alias.
   - The design either lists a control migration or drops those fields, and states the real number of candidates per alias.
   - Filters: `default_model` is an org field (`FirewallConfig`); `allowed_models` exists on keys and on the org, and both are retired in `firewall_v2_map.py:75-84`.
   - The design names the signed kind that carries each filter, and un-retires the fields.
8. **One alias grammar.** `[A-Za-z0-9._:/-]{1,128}`, the wire and gate grammar, is used in four places:
   - the wire;
   - the gate;
   - routing, where the cap is raised from 64 to 128;
   - the console serializer.

   `@` and `+` are not allowed. Case-fold collisions are omitted with `alias_collision`. The upstream id cap is 256, matching control.
9. **GW29 semantics.**
   - `om:` and per-org isolation key on `(org, alias)`.
   - Fleet `model:`, `iso:` and auto-isolation also match the resolved `(provider, upstream_model)`, so a fleet `model:gpt-4o` refuses org A's alias `prod-chat → gpt-4o`.
   - New key shapes go behind `KS_SCOPE_GENERATION` / `AMF_GATEWAY_MIN_SCOPE_GENERATION`.
   - The MODEL view is pinned with the plan **before** the phase-4b gate, and the gate reads the pinned view.
   - Tests: isolate A→B then delete B; an old-generation gateway keeps serving.
10. **Freshness.** The bound is projector `lag_ms` plus `fresh_ms`, which is 21 s with the defaults. It is asserted in the disable and rotation tests.
11. **Storage.** MODEL bodies are key-stored, not in `HASH_KINDS`.

## 7. F1a-3 requirements: dispatch and credentials (binding; design and review before code)

1. **Credentials.**
   - AES-256-GCM with a fresh `os.urandom(12)` nonce.
   - The AAD is a canonical length-prefixed encoding of `(namespace, org, row_id, alias, provider, base_url, kid)`.
   - The whole `{kid, nonce, ct}` triple is reused only when the plaintext and the AAD are byte-identical.
   - The encrypt kid is explicit, and switching it is gated on every gateway exporting `amf_gateway_cred_kids{kid}`.
   - A kid is retired only when nothing uses it and the fleet cursor has passed the re-wrap.
   - Kid rotation has a fixed cadence, so destroying a kid makes old log entries unreadable.
   - The projector now holds control's key and the gateway keys, so it is hardened like the gateways and reads its keys from `_FILE` secrets.
   - `GatewayAPIKey` secrets need a separate control key.
   - The Fernet builder's hash-derivation of a malformed key is refused in production.
2. **Leaks.**
   - The secret type drops the dataclass protocol, or an AST gate bans `asdict`/`astuple` on snapshot types (precedent: `vector_credentials.py:98`).
   - The projector validates the key charset (printable ASCII, no whitespace) and omits a bad row with `credential_invalid_format`.
   - Dispatch logs and traces exception type names only.
   - A planted-key grep covers logs, traces, audit rows and error bodies.
3. **SSRF.**
   - An address is refused when it is not `is_global` **or** `_blocked(...)` refuses it.
   - Embedded IPv4 addresses are decoded and re-checked (`64:ff9b::/96`, `64:ff9b:1::/48`, `2002::/16`, Teredo, `::/96`).
   - Private targets are allowed only through **per-org** `(org, host[:port])` entries, enforced at the projector and re-checked at connect.
   - Base URLs with userinfo, a query, a fragment, backslashes or dot segments are rejected.
   - Paths are joined structurally, and the final path is asserted to be `<base path>/<known endpoint>`.
   - The same grammar applies to `api_version`, Bedrock `region` and path segments in F1b/F1c.
   - The recorder is reachable only through a test overlay.
   - `http` is allowed only for org-scoped private entries.
4. **Pools.**
   - Pools are keyed by origin `(scheme, host, port)`, and default-provider origins are never evicted.
   - Distinct custom origins per org are capped.
   - The pool-acquire wait is bounded by the request deadline.
   - The client uses `trust_env=False`, an explicit `PinnedTransport` and `follow_redirects=False`, and has no default auth header.
   - Each org has an in-flight cap per origin.
   - The pool table is a refcounted LRU.
5. **Codes table (Reviewer 4 B1, Reviewer A B3).**
   - Every code used gets a row with HTTP status, error type, failure class and console stage/action, plus a `stages_to_console` test.
   - Existing codes are reused: `upstream_auth_error` (502) for BYOK 401/403 with `x-should-retry: false`, and `provider_unreachable`.
   - `PinnedRefused` gets a declared code.
   - The refusal keeps its candidate.
   - `routing_internal_error`, `model_not_found` and the D4 code are classified.
6. **v1 parity.**
   - Per-provider parameter shaping: a `drop_params` equivalent; `max_completion_tokens` for o-series models; `developer`, `seed` and `logit_bias` dropped where a provider rejects them; `include_usage`.
   - The upstream SSE decoder accepts comment keep-alives and `\r\n\r\n` framing.
   - Google's bad-key 400 maps to an upstream auth error.
   - The reasoning-model first-event timeout is contract-derived up to v1's 120 s.
   - Embeddings get a non-SSE upstream path.
   - Keyless Bedrock is refused like D4.
   - Inactive and keyless rows are omitted.
   - Vendor SSE quirks are tested with replayed captures.
7. **Observability.**
   - `pipeline_trace.routing` = `{requested_model, selected_model, route_destination=provider}`. It never includes `base_url`, and it needs a schema change in `console_schema.py:51`.
   - `amf_upstream_failures_total{provider,code}` uses closed vocabularies and is pre-seeded.
   - L36-1 metric deltas come from a single-worker gateway.
   - `routing_decision` logs carry `request_id`.
8. **Trust boundary (Reviewer B 13).**
   - The gateway's Valkey user is read-only on the signed-state key families, which needs ACLs and a separate user for the gateway.
   - Ed25519 state signatures are a follow-up.
9. **Compose `gateway-v2-prod`:**
   - `_FILE` secrets;
   - `ulimits.core: 0`;
   - port bound to 127.0.0.1;
   - `depends_on` healthy state services;
   - a stated `AMF_FD_LIMIT`.

## 8. F1d note (Reviewer B 14)

- The request-path Valkey families (audit, responses, budget) share the `{rv2}` hash tag, so in a Valkey Cluster they land on one slot.
- F1a-1 keeps the existing key layout. Every current deployment is a single Valkey node, and changing the audit key would also change control's ingest prefix.
- F1d moves the request-path families to per-org hash tags, migrates the control prefix with them, and reports Valkey operations per request.
- **Conditions for this deferral (Reviewer B):**
  - F1d lands before any Valkey Cluster deployment;
  - the F1a-1 live run records Valkey operations per request as F1d's baseline;
  - the audit lease keys sit outside `{ns}:audit:` (§5.4).

## 9. Risks (F1a-1)

| Risk | Mitigation |
|---|---|
| Engine swap changes what customer rules match | Translator from `re`'s own parse tree; a pinned `regex` whose Unicode database matches the interpreter's, checked at boot by a full functional comparison; exhaustive, fuzz and incremental-scan tests against `re` in CI. Two `regex` bugs are worked around and pinned by tests |
| The `regex` pin is frozen at 2023.8.8 (no cp312 wheel; upstream memory-safety fixes after it) | Built from the hashed sdist in a builder stage; never emits the constructs the known bugs need; crash fuzz in CI and an ASAN corpus weekly. **Exit plan:** explicit code-point sets built from `re`, which are exact on any `regex` release and 6–14× slower; the per-pass budget absorbs that |
| Inline streaming scans hold the loop | Incremental scanning (about 0.04 ms per delta for 10 rules, at any length, on punctuated prose and code); half a scan window of thread CPU per delta; an inline timeout moves the work off the loop instead of refusing; a per-org inline bucket caps each org's loop share at ρ |
| Off-loop scans of one org starve another | Scan scheduler: a FIFO per org, deficit round robin by thread CPU, at most T − 1 threads per org; depth bounded by admission |
| Hostile plans slow the projector or the gateway | Parse-only check in the projector (16.5 ms for a hostile 64-rule plan), memoised and time-boxed per round; expanded-repetition and case-insensitive-item limits per pattern and per plan; single-flight, per-rule, GIL-yielding lazy compile on the scheduler; byte-bounded program cache |
| A whole-text-rescan rule makes a very long stream expensive | Its rescans move off the loop and coalesce, so the stream slows (backpressure) instead of holding the loop. The org's scheduler share bounds what it takes from others. The worst case measured, Reviewer A's rules on 512 KiB of unpunctuated text, is about 79 ms per pass against a 400 ms budget |
| Detector factory touches every scan seam | Off-loop N5 test per path; the same objects re-exported for dev; full suite |
| A heavy but legitimate plan hits the inline per-delta budget | Nothing is refused inline: the work moves off the loop. A pass is refused only over its length-scaled per-pass budget, the same rule as non-streaming |
| Audit lease flaps between workers | Contract-derived TTL; the takeover test; adopting a decision is idempotent |

## 10. Disposition of the revision-2 reviews

| Finding | Where handled |
|---|---|
| A-B1 / B-10: RE2 drops Unicode semantics; timeout posture loosened | §5.2: `regex` for every pattern, no RE2, timeout keeps refusing, Unicode agreement test against `re` |
| B-5: inline streaming path on the loop | §5.2: inline cap of one scan window plus the per-request deadline; two-tenant streaming test |
| B-10: per-match thread, compile cost, no deadline | §5.2: thread removed; publish-time repetition limits; shared deadline; projector memo |
| A-B4 / R4-B2: audit tenants, sampler owner, assertions | §5.4 (PLAN-index tenant count, Valkey lease, fallback rule); §5.8 live assertions 4–5 |
| A-B3 / R4-B1: codes | F1a-1 adds none; §7.5 table for F1a-3 |
| A-B2 / R2-B2: GW29 semantics, rollout guard, pin before the gate | §6.9 |
| B-1: `serves_alias` O(orgs) | §6.3 |
| B-2: per-org failure isolation | §6.2 |
| B-3 / A-S4: projector cost, re-wrap, breaker, two-version edit | §6.6 |
| B-4: fleet-wide private allowlist, URL shape | §7.3 |
| B-6 / A-N3: NAT64 and embedded IPv4 | §7.3 |
| B-7: dumpable does not survive spawn; env-held keys | §5.6 (workers); §7.1 (projector, `_FILE` secrets) |
| B-8: unbounded caches, `_touched` race | §5.1 (program lifecycle); §6.4 |
| B-9: leak paths | §7.2 |
| B-11 / A-S1: rollout gate, rollback | §6.1 |
| B-12: pool keying | §7.4 |
| B-13: state integrity | §7.8 |
| B-14: single hash slot | §8 |
| A-S2: four alias grammars | §6.8 |
| A-S3: missing source columns, filter carriers | §6.7 |
| A-S5: freshness number, rollback scope | §6.2, §6.10 |
| A-S6: observability mis-mappings | §5.4.6 and §5.5 (gateway side); §7.7 |
| A-S7: v1 parity gaps | §6.5, §7.6 |
| A-S8: untested design items | §5.8 for F1a-1. F1a-2 and F1a-3 tests are part of their design reviews |
| A-N1: `bind_surface` count | §3: 9 calls in 6 modules |
| A-N2: pool divisor | §7.4 (per-origin pools replace the divisor model) |
| A-N4: detector cache | §5.1 program lifecycle |
| A-N5: moderations | §5.1: behaviour unchanged |
| A-N6: `_redos_shape` | §5.2: unchanged |
| A-N7: dependencies | §5.2: `regex` in base deps, pinned |
| A-N8, A-N9, B-15…B-19 | §6.11, §7.1, §7.3, §7.7 |

Revision-1 dispositions (reviewers 1–5) are unchanged except where the table above supersedes them.

### Revision-3 findings (Plan Reviewers A and B at `91785d9b`)

| Finding | Where handled |
|---|---|
| A-B1 / B-S: `regex` V0 is not `re` (`\s` U+001C–1F, `\w`, `\d`, `\b` with combining marks, i/ı/İ folding, fuzzy and POSIX syntax) | §5.2: translator from `re`'s parse tree, a Unicode-matched `regex` pin checked at boot, the case-fold verdict taken from `re`, exhaustive and fuzz evidence |
| A-B2 / B-1: the per-request deadline refuses long streams and slow upstreams; `remaining ≤ 0` disables the timeout | §5.2: no aggregate or wall-clock budget; per-pass budget off-loop; half-scan-window per-delta inline budget; refuse without calling at `≤ 0` |
| A-B2 / B-2: isolation shown only for streaming; R rules × cap; C concurrent streams | §5.2: every non-streaming scan moves off the loop; the per-delta budget is shared by all rules; per-request loop cost ≤ one scan window; tests (a)–(c) |
| B-3: compile cost unbounded; no real per-plan cap; admin bypasses the serializer; eager compile | §5.2: repetition product limit, 64 rules per plan, enforced in control, projector and gateway; lazy off-loop compile with an LRU |
| A-S3 / B-4: sampler key collides with an org slug; decision key unnamed; renewal not compare-and-set | §5.4: `{ns}:auditctl:*` keys, Lua compare-and-expire, decision age check |
| A-S4: unknown budget is the R2-05 configuration | §5.4: production requires a budget, or readiness fails |
| B-S: shutdown loses the in-flight batch; drain may never run; readiness blind to a dead writer | §5.4: stop after batch, graceful-shutdown timeout, drain bound under multiprocess, done-callback |
| A-S1: DoS tests never time out; timeout overshoot | §5.8: patterns that time out under `regex`; half-window cap |
| A-S2: removing `request_org` can fail open; RAG tier-1 not named | §5.1: tier-1 built by the factory; `bind_surface` refuses a program-less detector; test over all sites |
| A-S5: output plan rule and per-plan cap untested | §5.8 |
| A-N1, N2, N3 | `BASE_DETECTORS` comes from PR #104; the per-plan cap is a new named constant; control does not compile with `regex` (§5.2 Placement) |
| A-N4: shutdown drops a batch | §5.4 |
| A-N5: live setup said one worker but tested two | §5.8: one worker; responses are unit-tested |
| A-N6, A-N7 | §6.2 KEY cold-fetch wording; §6.1 rollout floor enforced |
| A-N8: moderations with no plan | §5.1 |
| A-N9: typo | §3 |
| B: projector notes logged `base_url` | §6.6 |
| B: hash-tag deferral conditions | §8 and §5.8 live step 7 (Valkey ops baseline); lease keys moved (§5.4) |

### Revision-4 findings (Plan Reviewers A and B at `9df2980c`)

| Finding | Where handled |
|---|---|
| A-B1 / B-B3: translation cost unbounded and on the projector's pre-heartbeat path; compile cost of sequential repetition; LRU sized by entries; executor translation stalls the loop | §5.2.1: one-`findall` case-insensitive verdicts and a build-time cased table. §5.2.3: expanded-repetition and case-insensitive-item limits (per pattern and per plan); a parse-only projector check, memoised, time-boxed and out of `desired_state`; single-flight, per-rule, GIL-yielding compile on the scheduler; byte-bounded cache. All with measured costs |
| A-B2 / B-B1: the per-delta scan rescans the whole output; translated `\b` next to a class is slow | §5.2.1: exact one-sided boundaries. §5.2.4: incremental scanning with two exact finality rules (reach and stoppers); inline timeouts move work off the loop instead of refusing; length-scaled per-pass budget. §5.8: performance gate |
| B-B2: overshoot; unbounded concurrent streams; shared executor | §5.2.4: the "overshoot" is thread-CPU accounting under host contention, so every budget is in thread CPU. §5.2.5: per-org inline bucket and scan scheduler (DRR, T − 1 cap). §5.8 tests (a)–(d) |
| A-S1: possessive repeats over groups | §5.2.1: `(?>(?>X){m,n})`; possessive repeats in the CI fuzz |
| A-S2 / B-4: no cp312 wheel; `regex.__version__`; `requires-python` | §5.2.2: builder stage with a hashed sdist; `importlib.metadata`; full functional boot check; `==3.12.*` |
| B-4: missed upstream fixes; frozen pin; the wrong "5,004" | §5.2.2: unreachable constructs named; crash fuzz and ASAN corpus; exit plan; corrected counts. §9 |
| A-S3: STRICT_WITHHOLD scans on the loop when `runner` is `None` | §5.2.5: the scheduler is always the runner. §5.8 test (c) covers strict mode |
| A-S4: the aggregate loop-time bound was per request, not per delta | §5.2.5: per-org inline bucket. §5.8 test (a) with C concurrent streams |
| A NIT 1: stale per-request deadline text; dependency bullet | §4 and §5.1 updated; §5.2.2 says where the pin and `types-regex` live |
| A NIT 2: the fuzz hangs inside `re`; `SystemError` | §5.8: worker processes with a per-pattern timeout; `SystemError` treated as a refusal |
| A NIT 3: HEAD fail-open on `SystemError` | §5.2.1 and the §5.8 regression test |
| A NIT 4: `BASE_DETECTORS` | §5.1: on `develop` since #104 |
| B NIT 5: §6.6 `base_url` | §6.6 |
| B NIT 6: revision-3 NITs 9 and 11 | §5.6 (parent hardening, runbook); §5.4 (reserved `_` slugs) |
| B NIT 7: `perf.md` input text; the `\b`+class family | `perf5.md` states its inputs and includes the family |
| B NIT 8: `_CASED` computed at import | `gen_cased.py` builds the table, with a CI `--check` |
| (found in revision 5) `regex` merges lone negated characters in an alternation | §5.2.1: the duplicated-member workaround; `alt_exhaustive.py` and the extended fuzz |

---
_Generated by [Claude Code](https://claude.ai/code/session_01SDCVQVj8LRKW3CZ99zfdW1)_
