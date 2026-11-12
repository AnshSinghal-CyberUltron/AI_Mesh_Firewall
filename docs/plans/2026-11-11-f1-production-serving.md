# F1: production gateway serves real completions (GW36 S5 + per-org providers)

Status: 12 Nov 2026, **revision 6**.
- Revision 1 (`8484c571`) was challenged by five reviewers.
- Revisions 2 (`4d23ed07`) and 3 (`91785d9b`) were challenged by Plan Reviewer A (completeness) and Plan Reviewer B (security, scale and operability). Both requested changes each time.
- Revision 4 replaced the customer-regex design (§5.2) with one backed by a working prototype and measurements, and fixed the other revision-3 findings (§10).
- Revision 5 answered the revision-4 reviews (§10): exact one-sided word boundaries, an incremental streaming scan, a tenant-fair scan scheduler, a parse-only projector check and new cost limits.
- **Revision 6 answers the revision-5 reviews** (§10, "Revision-5 findings" and "Rev 6 change log"), plus the owner decisions of 12 Nov (§2 D6–D8) and issues #125/#126. Sections changed: §2, §3 (the live nesting bomb), §4 (v1 untouched), §5.1 (no compile on the applier), §5.2, §5.4 (reserved slugs on the v2 side), §5.8, §9 and §10. In §5.2:
  - **the timeout clock:** stock `regex` times out on process CPU, so it is patched to thread CPU, built reproducibly from the hash-pinned sdist, and checked at boot (§5.2.2). The two consequences, the GIL convoy (wall time) and thread-CPU inflation (headroom), are stated and measured (§5.2.4);
  - **new limits:** group nesting depth, translated size and compile units, per rule and per plan; every exception is a refused rule (§5.2.3). Building them found that `regex` unrolls repeat minimums (a GIL-holding compile bomb that passed every revision-5 limit);
  - **range classes in O(log n)** and a translation CPU cap (§5.2.1); **hold and retry** for timeouts on non-final passes (§5.2.4); a **worker-wide inline bucket** and the DRR bound as a formula (§5.2.5);
  - every revision-6 number was re-run on the 12 Nov host under the heavy lock, on both the unpatched and the patched wheel (`ev6/RERUN.md`).

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
| D6 (12 Nov) | **v1 production (`gateway/`) is not touched until the v2 revamp is complete.** F1a is v2-only; no F1a step may change v1 behaviour (§4) | owner |
| D7 (12 Nov) | Code PRs do not run full suites locally: CI on GitHub runners is the venue, using GitHub Enterprise compute (standard and larger runners). Local runs on the shared 2-CPU host are evidence, not suites; evidence too big for that host becomes a CI job (§5.8) | owner |
| D8 (12 Nov) | The F1a-2 and F1a-3 owner questions are answered with the proposed defaults; they are recorded in those slices' own design PRs (#118, #121), not here | owner |

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
- **A live fleet-wide DoS (Reviewer B, revision 5, blocker 2).** A customer pattern nested 499 deep (`"(" * 499 + "a" + ")" * 499`, 999 characters, no repeats) raises `RecursionError` in `re`. `validate_plan` catches only `ValueError`, `UnicodeEncodeError` and `CompileError` (`state_control/projector.py:565,578`) and `_prepare` only `re.error` (`detect/conditions.py:108-111`), so the exception escapes `round_once`, the projector heartbeat ages, and after `AMF_PROJECTOR_LAG_MS` every gateway fails closed, every round while the row exists. PR #117 is the HEAD hotfix; F1a-1 keeps its guarantee (§5.2.3).
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
| **F1a-1 Pipeline safety** | Detector factory (N5), customer-regex engine (exact translator, a thread-CPU-patched `regex` pin, incremental streaming scan, tenant-fair scan scheduler), production resolvers and base detectors, bounded audit sink, N14, GW35, response store, per-worker process hardening, honest boot log. Provider stays a stub | **§5, ready for implementation** |
| **F1a-2 MODEL state** | Forward-compatible stamp (its own release first), MODEL kind, projector, per-org applier, catalogue, GW29 semantics, alias grammar, filters | **§6 requirements**; own design and review before code |
| **F1a-3 OpenAI-compatible dispatch** | Credentials, SSRF, pools, parameter shaping, error mapping, observability, compose `gateway-v2-prod` | **§7 requirements**; own design and review before code |
| F1b | Azure OpenAI and Anthropic native adapters | after F1a-3 |
| F1c | AWS Bedrock (SigV4, event stream) | after F1b |
| F1d | Load and scaling curves, Valkey sharding | last |

Every slice ships as its own PR into `develop`, with:
- the full gateway_v2 suite and the control suites (ratchet), run by CI on GitHub runners, not locally (D7);
- two agent reviews and an agent merge;
- a live check where the slice is user-visible.

**v1 is untouched (D6).** v1 (`gateway/`) is the live production data plane: prod compose builds `gateway/Dockerfile`, nginx sends `/v1` to `gateway:8300`, and `gateway-v2` is profile-gated off in prod. No F1a-1 step changes v1's behaviour:
- **No code under `gateway/` changes**, and neither its compose service, its image, its nginx route nor the `gateway-v2` profile gate.
- **Control stays v1-compatible.** v1 reads the same rules from control (`POLICY_SYNC`, `gateway/ai_mesh_gateway/policy_engine.py`), so F1a-1 does not change what control accepts at save for anyone:
  - the customer-regex limits of §5.2.3 are **not** save-time refusals during the revamp. Control computes the v2 verdict at save and returns it as a non-blocking warning ("refused by the v2 gateway: <reason>"), shown in the console; the v2 projector and gateway refuse the rule for v2 only. Making the verdict a save-time refusal is a post-revamp item;
  - reserved `_` org slugs (§5.4) are handled on the v2 side; control does not refuse them and no migration is blocked;
  - `_matches_own_pattern` (`firewall_v2_publish.py`) is on the v2 publish path only;
  - `ai_mesh_shared/regex_limits.py` is a new module imported only by control's v2 verdict. v1 imports `ai_mesh_shared` but not this module.
- PR #117 (the live HEAD hotfix for the nesting bomb) is not part of F1a-1; whether its control-side depth refusal is acceptable for v1 is decided on #117.

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
  - The program's **entry** is created when the PLAN record is applied, next to `plan_from_record`, and holds the parse-only verdicts only. Translation, `regex.compile` and the REWRITE template check are **lazy**, on the scan scheduler, charged to the org (§5.2.3); the state applier never translates, compiles or scans a customer pattern (issue #125: a slow REWRITE check stalled every gateway's applier for about 1.9 s per apply). It is stored in a per-worker `ConditionPrograms` map keyed by org, holding `(plan version, program)`.
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
2. **Pre-emption:** a catastrophic pattern is stopped within a bound of **the scanning thread's own CPU time**, whatever the worker's other threads are doing (Reviewer B, revision 5, blocker 1).
3. **Event-loop bound:** no tenant takes more than a stated share of a worker's event loop, and all tenants together take no more than a stated worker-wide share (B-3).
4. **No false refusals (restated in revision 6, Reviewer A S1):** a stream is refused for regex cost only where the same text, scanned once non-streaming, would be refused, **or where a rule stays undecided over 4 KiB of held text** (§5.2.4). Long streams, slow upstreams and many ordinary rules are not refused. Thread CPU of identical work varies by up to 1.7× on a contended host, so this holds for passes that cost at most about half their budget on a quiet host; §5.8's performance gate enforces that on the corpus (§5.2.4, headroom).
5. **Bounded translation and compile cost,** kept off the projector's safety path: nesting depth, translated size, compile units and translation CPU are capped per rule, and the sums per plan.
6. **Tenant isolation:** one tenant's rules cannot starve another tenant's scans, on the loop or off it. One tenant's threads cannot shrink another tenant's budget.
7. **Nothing escapes (new, B-2):** any exception while checking, translating or compiling a rule (`RecursionError`, `MemoryError`, `SystemError`, `OverflowError`, anything else) makes that rule a refused rule. It never escapes control's v2 verdict at save, a projector round or a gateway compile.

**Why the obvious engines fail (measured by the reviewers and re-measured here).**
- **RE2** has ASCII-only `\d`, `\w`, `\s` and `\b`.
- **`regex` in VERSION0** differs from `re`:
  - `\s` misses U+001C–U+001F;
  - `\w` differs on about 25k code points;
  - `\b` differs next to combining marks;
  - case folding differs (`re` folds i/ı/İ and k/K/K together);
  - it reads fuzzy `{e<=1}` and POSIX `[[:alpha:]]` syntax where `re` reads literals.
- **`re`** cannot be pre-empted.
- **Stock `regex` 2023.8.8 times out on process CPU,** so one tenant's scan threads shrink another tenant's budget. It is patched (§5.2.2).

**Evidence.** Revision 5's prototype is in `docs/plans/evidence/2026-11-11-f1a1-regex/` (`ev5/`). Revision 6's prototype, the patched `regex` build and every revision-6 measurement are in `docs/plans/evidence/2026-11-12-f1-plan-rev6/` (`ev6/`): `RESULTS.md` indexes it, `LIMITS6.md` covers limits, compile cost and scheduling, `clock/CLOCK.md` covers the timeout clock, and `RERUN.md` records every revision-6 run (command, duration, result, host load, what else was running). All times are thread CPU unless marked wall, on a shared 2-CPU host, Python 3.12.3, `regex` 2023.8.8. Two hosts were used: the 11 Nov host (`clock/results/*.txt`) and the 12 Nov host (`RERUN.md`, `r6_*.txt`, `clock/results/host2_*.txt`); each number below names its host where it matters.

#### 5.2.1 The translator

- **Parsing.** An iterative O(n) scan first measures group nesting, following `re._parser` token by token (escapes, sets with a leading `]`, `(?#…)`, verbose comments, scoped verbose flags, conditionals). A pattern nested deeper than 16 is refused **before** any recursive parser runs (§5.2.3). The pattern is then parsed by `re`'s own parser (`re._parser`). Escapes, sets, verbose mode, scoped flags and quantifier syntax therefore mean what `re` says.
  - The tree is re-emitted for `regex`, and no global flag reaches `regex`.
  - `.`, `^` and `$` get explicit scoped flags.
  - Braces and brackets are emitted only as `re` parsed them, so fuzzy and POSIX syntax cannot appear.
  - **The emitted vocabulary is fixed:** literals, escapes, sets, `\p{…}` classes, groups, alternation, counted, lazy and possessive repeats, atomic groups, lookarounds, `\A`, `\Z`, scoped flags, backreferences and conditionals. Fuzzy matching, recursion, `(?r)` reverse search and `(*SKIP)`/`(*FAIL)` verbs are never emitted, so the `regex` bugs confined to them (#525, #607, #608) are out of reach.
- **Categories.** `\w` becomes `[\p{L}\p{N}_]`, `\d` becomes `\p{Nd}`, and `\s` becomes an explicit 10-range set taken from `re`. Negations follow the same pattern. They are never case-folded, because `re` matches a category by the character itself. Inside sets, the categories are split out of the set.
- **Word boundaries.** `\b` and `\B` are lookarounds over that `\w`, and `\B` is false on the empty string, as in `re`.
  - When the neighbouring item can only start (or end) with a word character, or only with a non-word character, one side of the boundary is already known, and the translator emits **one** lookaround for the other side: `\b[A-Z]` becomes `(?<![\p{L}\p{N}_])[A-Z]`. This is exact (revision 5; A attacked it with 172,000 boundary-heavy patterns, 0 mismatches). Anything else keeps the two-sided form: a nullable neighbour, `.`, a negated set, or a mixed class such as `(?i)ι`, whose variant U+0345 is not a word character.
  - The IBAN-shaped `\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b` takes 0.03 ms on 32 KiB of prose and 0.23 ms on code, against 0.48 and 0.58 ms for `re`.
  - **Range classes in O(log n) (Reviewer A, revision 5, B1).** Revision 5 decided whether a range was all-word by building every code point of it in Python (ranges up to 65,535 wide). That cost was counted by no limit: A's 166 distinct wide ranges (`[Ā-￯]+[ĥ-￯]+…`, 996 characters) took 986 ms to translate, and 6 such rules held a worker for 5.6 s. Revision 6 bisects `WORD_FLIPS`, the 1,496 code points where `re`'s `\w` membership flips, generated at build time into `_cased_table.py` by `gen_cased.py` and checked by the CI `--check`. Measured on the 12 Nov host, same run, cold caches: A's 166 wide ranges translate in **3.2 ms** (revision 5 at `e2c3e2cf`: 892 ms), the `\b[Ā-￯]…` form in 1.9 ms (763 ms); A's harness, 6 such rules compiled one per executor step against a loop ticking every 1 ms, costs the worker **48.8 ms** of CPU with loop lateness max 6.9 ms (revision 5: 5,388 ms of CPU over 6.5 s of wall, the loop at 100 ticks/s, lateness p99 45.5 ms, max 108 ms; A measured 5.6 s, 105 ticks/s and a 226 ms max). The parse-only check of A's pattern costs 4.8 ms (revision 5: 1.0 ms). The bisect is exact: against a brute-force classification by `re` over every code point of the range, 0 mismatches on 26,586 ranges (every flip's neighbourhood, every run between flips, 20,000 random widths) (`ev6/range_b1.py`, `r6_range_b1.txt`). A surrogate counts as non-word, as in `re`, so the bisect can only be more conservative than revision 5's probe (a mixed verdict keeps the exact two-sided form).
  - **Translation CPU cap.** The gateway translates each rule under a thread-CPU cap of 50 ms (`TRANSLATE_CPU_BUDGET_S`); a rule that needs more is refused, fails closed and is counted (`amf_regex_rules_refused_total{reason="translate_cpu"}`). It is a safety net for a shape the limits do not count: the worst legal rule measured takes 16.9 ms on the 12 Nov host (24.2 ms on 11 Nov at a higher load) (64 distinct case-insensitive sets, `perf6.md`), under half the cap, so a 1.7× contention inflation stays inside it. Control and the projector do not translate, so a rule refused only by this cap shows as a gateway refusal with that reason, never silently.
- **Atomic repeats, where exact.** A greedy repeat of one character class X is emitted atomically when the next item must start with a character of the other word class. Before `\b` this also needs at least one X to remain.
- **Possessive repeats.** Emitted as `(?>(?>X){m,n})`: `re` backtracks neither into a possessive repeat nor into any one of its iterations.
- **Case-insensitive literals and sets.** These use `regex`'s native case folding. The translator then takes `re`'s verdict on every one of the 2,927 cased code points, and adds or removes the code points where the two engines disagree. That keeps `re`'s special folds exact.
  - **Cost.** The verdict is one `findall` over a string of all cased code points, by `re` and by `regex`. Revision 6 re-checks only the missing points behind the prefilter guard (a full guarded scan would cost 14× more). The cased-code-point table is generated at build time, not at import.
  - **The patch is bounded (new).** The add and drop sets only ever contain the 10 code points where the two engines' folding differs (`FOLD_DISAGREE`, now generated into `_cased_table.py`) and an astral literal member with its case partners (next bullet). The parse-only estimate pads by that maximum (§5.2.3); `estimate_test.py` checks the patch points against it on every cased literal and 20,000 random sets.
  - **`re`'s own astral bug is kept (new).** CPython 3.12's `re` drops an astral literal from a multi-member case-insensitive set: `(?i)[\U0001e91c\x41]` does not match U+1E91C (`_compiler._optimize_charset` keeps the literal unfolded in the tail). The translator reproduces `re`'s verdict, as for every case-insensitive item, because requirement 1 is `re`'s meaning.
- **Three `regex` bugs, worked around** (present in 2023.8.8; the first two also in 2026.9.29):
  - **The first-character prefilter.** When a pattern starts with an optional case-insensitive item, `regex` builds a prefilter that folds the following case-sensitive set; `(?i:a)?[^\p{L}]` misses U+0345. Patterns containing a case-insensitive part get a leading `(?=[\s\S]|\Z)`, which disables that prefilter. **Cost (new):** on case-insensitive keyword lists the guard makes the translation 4–8× slower than `re` (`(?i)\b(?:invoice|…10 words)\b`: 13.6 ms with the guard, 2.3 ms without, `re` 2.2 ms, 32 KiB of prose). Bounded by the per-pass budget and charged to the org; narrowing the guard to patterns that can start with an optional case-insensitive item is a tracked follow-up (§9), because it changes the output and needs the whole exactness suite.
  - **Lone negated characters in an alternation (widened, Reviewer A, revision 5, NIT 1).** `regex` merges alternatives that are single negated characters into one negated set, so `(?:[^9]|[^c])` matches only characters that are neither, instead of every character: an under-match, so a bypass. It fires through non-capturing groups (`(?:(?:[^9])|(?:[^c]))`) and with astral members (`(?:[^\U0001d7ce]|[^a])`), and, found in revision 6, with **one-code-point ranges**: revision 5 emitted `(?:[^a-a]|[^b-b])` unchanged, and it under-matched (12,548 matches against `re`'s 12,550). Revision 6 turns a one-code-point range into a literal and emits **every** negated set that covers one code point with its member repeated (`[^\x39\x39]`), however the pattern spells it; every negated set goes through one function. The raw bug still fires on the pin, so the workaround stays.
  - **Repeat minimums are unrolled (new).** `regex`'s `build_REPEAT` (`_regex.c:24906-25090`) builds a repeat's body *min* times, plus one looping copy when the maximum is larger, so nested repeats multiply the compiled program. `(a)+` nested 16 deep (49 characters, passing every revision-5 limit) compiled in 166–310 ms in **one C call that holds the GIL** and retained 113–181 MiB; nested 32 deep it ran the host out of memory. `re` compiles it in 0.2 ms. The compile-units limit (§5.2.3) bounds it.
- **Refused at translation:** the ASCII and LOCALE flags, a backreference under IGNORECASE, a **variable-width lookbehind** (new: `re.compile` refuses it after parsing succeeds; revision 5 translated `(?<=a+)b`), and anything over the limits in §5.2.3. Every refusal is decided on the parse tree, so the parse-only check refuses exactly what translation refuses, and every exception is a refusal (requirement 7).
- **A fail-open at HEAD, removed.** On 3.12.3, `re` raises `SystemError` for some possessive patterns with a capture group, and `conditions.py:_work` catches only `re.error`, so the rule silently matches nothing. The v2 gateway no longer compiles customer patterns with `re`; control's v2 verdict treats any exception as a refusal (a warning at save, §4); a regression test pins both.
- **Evidence (exactness),** on the revision-6 translator. Two wheels: the unpatched pinned build (`NOPATCH=1`, `.so` `d07e53d1…`) and the patched build (§5.2.2, `.so` `64a8bac6…`). Both runs on the 12 Nov host under the heavy lock (`RERUN.md`):

  | Check | Unpatched wheel (`r6_*.txt`) | Patched wheel (`clock/run-2/`) |
  |---|---|---|
  | `\d \D \w \W \s \S`: in and out of sets, negated, with and without `(?i)`, every code point | 0 mismatches | 0 mismatches |
  | `\b \B \bx x\b \Bx x\B` (two-sided) next to every code point | 0 mismatches | 0 mismatches |
  | One-sided boundaries: 30 patterns × 2 flag sets, each neighbour class interleaved with every code point, plus the text edges | 0 mismatches | 0 mismatches |
  | Every case-insensitive single literal (2,927) against every code point | 0 mismatches | 0 mismatches |
  | Every pair and triple of a 19-item single-character vocabulary in an alternation (plain, repeated, negative lookahead, after a literal; no flag, `(?i)`, `(?s)`) | 0 mismatches in 86,640 patterns | 0 mismatches in 86,640 patterns |
  | Every spelling of a one-code-point negation in alternations (`alt_negated.py`) | 0 mismatches in 65,040 patterns; no one-code-point negated set emitted | 0 mismatches in 65,040 patterns; none emitted |
  | Differential fuzz against `re`, seeds 11–13 × 6,000 patterns (worker processes; a pattern that hangs inside `re` is skipped and counted) | 0 mismatches in 133,544 pattern-text checks; 10 `re` hangs skipped; 6 `regex` 1 s timeouts where `re` took 0.44–9.5 s | 0 mismatches in 133,552 checks; 8 `re` hangs skipped; 7 `regex` timeouts where `re` took 0.48–13.1 s |
  | Incremental scan against a whole-text rescan after every delta, seeds 11–13 | 0 mismatches: 15,038 incremental streams, 246,809 deltas; 27 skipped on a `regex` timeout | 0 mismatches: 15,038 streams, 246,809 deltas; 27 skipped |
  | Depth scan against `re`'s own recursion; bombs; 16-deep compile from 600 frames; injected exceptions (`nesting_test.py`) | exact on 7,505 accepted patterns; 0 failures | exact on 7,505; 0 failures |
  | Real ≤ estimate; patch bound; accepted ⇒ `re.compile` accepts (`estimate_test.py`, 3 × 3,400) | 0 below the estimate, 0 violations | 0 below the estimate, 0 violations |
  | Range classifier against brute force (`range_b1.py`) | 0 mismatches on 26,586 ranges | (pure Python; wheel-independent) |
  | S1 policy tests (`s1_test.py`) | pass | pass |
  | `gen_cased.py --check` | exit 0 | exit 0 |

  Revision 5's 9-seed fuzz (401,408 pairs, 0 mismatches) and 8-seed incremental fuzz (655,969 deltas, 0 mismatches) stand for the revision-5 translator; the seeds above re-run the grammar on revision 6.

  **Match speed against `re`.** On the 15-pattern corpus (32 KiB of prose and of code, including the `\b`+class family) the worst ratio is 3.7× (`\b\w+\s+(?:token|secret)\b`; 3.8× in the 12 Nov `perf6.md` run), and 11 of the 15 are faster than `re`. Outside the corpus (new, `LIMITS6.md` §2.4): case-insensitive keyword lists 4–8× (the prefilter guard, above) and possessive nests 4–10× (`(?>(?>X)*)`). Both are bounded by the per-pass budget and charged to the org; §5.8's gate corpus gains them with their own allowed ratio.

#### 5.2.2 The `regex` pin, the clock patch and the supply chain

- **The Unicode database must match.** `regex` is pinned to **2023.8.8**, whose Unicode database (15.0) equals Python 3.12's. On that pair, `[\p{L}\p{N}_]` equals `re`'s `\w` on all 1,114,112 code points, and `\p{Nd}` equals `\d`. Later releases differ (measured on Python 3.12):

  | `regex` | `\w` differs on | `\d` differs on |
  |---|---|---|
  | 2023.10.3 to 2024.5.15 | 622 | 0 |
  | 2024.11.6 | 5,004 | 80 |
  | 2026.9.29 | 22,484 | 90 |

- **The timeout clock is patched (Reviewer B, revision 5, blocker 1 and NIT 6).** Revision 5 said `regex`'s timeout counts the matching thread's CPU. For stock 2023.8.8 that is false.
  - **Stock.** `state_init` starts the clock with C `clock()` (`_regex.c:18337`), `check_timed_out` tests `clock() - start_time < timeout` (`:2211`), and `decode_timeout` converts with `CLOCKS_PER_SEC` (`:20791`). glibc's `clock()` is `CLOCK_PROCESS_CPUTIME_ID`: the CPU of **every** thread of the worker. So while org A's pathological passes run on the other scan threads, org B's ordinary pass (3 rules over 128 KiB, about 57% of its 100 ms budget alone) is refused 7/20 at T = 2 and 20/20 at T = 4 and 8 (11 Nov host); 0/20 at T = 2 (where B's pass alone cost 60%), 20/20 at T = 4 and 8, with org A's own passes refused at 49, 26 and 14 ms of their CPU, about budget ÷ busy threads on the 12 Nov host. Upstream's latest release (2026.11.9, published 9 Nov) still uses `clock()`, so there is no upstream fix to move to, and moving would lose exact `\w` anyway.
  - **The patch** (`ev6/clock/regex-2023.8.8-thread-cputime.patch`, +56/−6 lines in `_regex.c`, sha256 `cb544353400b9870ff8d3018d0c7bf783873f18447566d5bd899783e8ef3cd7d`) reads `clock_gettime(CLOCK_THREAD_CPUTIME_ID)` in microseconds at the two sites, so `timeout=` keeps its unit; `start_time` becomes `int64_t` (no 32-bit `clock_t` wrap). It fails closed: a timed iterator advanced on a thread other than the one that created it raises `TimeoutError` at its first check; an unreadable clock, or one that reads earlier than its start, counts as timed out. `decode_timeout` raises `ValueError` on NaN (stock: no limit) and clamps huge or infinite values (stock: they fire at once, through an undefined double-to-integer conversion). A negative timeout still means no timeout, as upstream, so §5.2.4's guard stays. It does not build without `CLOCK_THREAD_CPUTIME_ID` (Linux; macOS ≥ 10.12), rather than silently falling back to process CPU.
  - **Measured with the patch.** B's pass refused 0/80 with 0, 1, 3 and 7 org-A threads (11 Nov host) and 0/80 again (B alone, and 1, 3 and 7 org-A threads; B's own CPU per pass median 59–65 ms; org A refused at 100.3–100.5 ms) (12 Nov host); org A's own passes were refused at exactly their budget (100.3–100.4 ms of their CPU). The timeout fires within +0.5 ms of its value in the matching thread's CPU whatever else runs (stock fired at timeout ÷ (busy threads + 1)). A clock read costs 204–242 ns, against 216–279 ns for `clock()`; match speed patched/unpatched over perf5's 30 pairs is 0.96. Upstream's 100 tests pass on the patched build (3.12.3, and 3.12.16 with no network).
  - **The pin is therefore a patched fork.** It is recorded here and in §9, with the patch file, its sha256 and the evidence suites that must pass on the built wheel (§5.8).
  - **Alternative kept as the fallback: scans in worker processes.** In a single-threaded worker process stock `clock()` equals the scan's CPU, so no fork is needed. Measured: IPC ≤ 1.5 ms (wall median) per pass at 512 KiB; but each worker holds its own compiled cache (21.7 MiB base, +66 MiB for a 64-rule plan of compile-heavy shapes at the limits) and pays its own compile, and pinning orgs to workers brings back head-of-line blocking. Processes also give crash containment and a hard kill. The plan switches to them if the crash fuzz or ASAN ever finds a reachable memory-safety bug in the pin (`clock/CLOCK.md` §7).
- **The build (Reviewer B, revision 5, should-fix 5).** PyPI has no cp312 wheel for 2023.8.8, and the runtime image has no compiler. A builder stage builds the wheel with `ev6/clock/build_patched_regex.sh`, for linux/amd64 and linux/arm64:
  - the sdist is pinned by sha256 (`fcbdc5f2b0f1cd0f6a56cdb46fe41d2cce1e644e3b68832f3eeebc5fb0f7712e`) and the patch by sha256, both verified before anything is unpacked; the patch applies with `--fuzz=0`;
  - the build backend is pinned by version **and hash** (`setuptools==80.9.0`, `build-requirements.txt`), installed with `--require-hashes --only-binary :all:` into an isolated venv, and the PEP 517 hook is called directly: no build frontend, no build isolation, no network. Found: under pip's default build isolation, `--require-hashes` does not cover the backend; it installed an unpinned setuptools 84.2.1 into the isolated build environment;
  - found: **any `CFLAGS` in the environment, even an empty one, replaces the interpreter's `-DNDEBUG -g -O2 -Wall`** (setuptools' distutils), building at `-O0` with `assert()` live. The script prepends the interpreter's flags, never exports an empty `CFLAGS`, and fails if the compile line lacks `-O2` or `-DNDEBUG`;
  - **reproducible:** `SOURCE_DATE_EPOCH=1691527080` (the sdist's newest mtime) and `-ffile-prefix-map`. The patched wheel's sha256 is `8ca6147610412de1a99bef3ac300d603698c394398d1c9fb0ed31035d34a36da` from three builds on the 11 Nov host and from two builds at two new paths on the 12 Nov host; the unpatched control (`NOPATCH=1`) reproduced too (`5bea7ab3…c8790`). This holds per toolchain (gcc 13.3.0, glibc 2.39, CPython 3.12.3 headers); a Debian builder gives a different, but again stable, hash. **CI rebuilds the wheel and requires the recorded sha256 per architecture**, so a cache rebuild reproduces the wheel or fails;
  - **the builder image is pinned by digest** and must carry a compiler; its digest is recorded next to the wheel hash. Not resolved here: Docker Hub rate-limited the manifest query on 11 Nov, and the 12 Nov host has no Docker. It is an F1a-1 build-job item, with the digest recorded in the PR that adds the builder stage;
  - the runtime stage installs the wheels with `--no-index --require-hashes`. No compiler reaches the runtime image;
  - OSV lists no advisory for `regex` (Reviewer B, 11 Nov).
- **Where the pin lives.** `regex==2023.8.8` is a base dependency in `gateway_v2/pyproject.toml`, resolved to the built wheel by hash, and `types-regex` is a dev dependency for `mypy --strict`. `requires-python` becomes `==3.12.*`, matching the boot check.
- **Boot check.** The gateway refuses to start unless all of these hold:
  - `importlib.metadata.version("regex") == "2023.8.8"`;
  - `unicodedata.unidata_version == "15.0.0"`;
  - **(new) the extension is the patched build:** the sha256 of `regex._regex.__file__` equals the CI-recorded hash for the architecture, and the functional marker `regex.compile("a").search("a", timeout=float("nan"))` raises `ValueError` (only the patched build does; measured on both builds). Stock 2023.8.8 also reports version 2023.8.8, so without this a stale-cache stock wheel would silently bring back the cross-tenant refusals;
  - a full functional comparison: `[\p{L}\p{N}_]`, `\p{Nd}` and the `\s` set against `re`'s `\w`, `\d` and `\s`, by `findall` over every code point (0.27 s of CPU per process).
- **Upstream fixes after 2023.8.8.** Later releases fixed memory-safety bugs in compile and scan code that translated patterns can reach: #611 (a heap out-of-bounds write at compile time) and #612 (a size underflow), both in 2026.8.30; a segfault fix in 2026.3.32; allocation and error-propagation fixes in PRs 615–618. Whether #611 and #612 exist in 2023.8.8 is not confirmed. Therefore:
  - **Crash fuzz in CI** on the built (patched) wheel: translator output from the fuzz grammar plus the limit-edge shapes, compiled and matched in child processes; a death by a signal fails the job and records the pattern;
  - **ASAN + UBSan** on an ASAN build of the patched wheel (`ASAN=1`), weekly and on every PR that touches the patch, the pin or the translator. Found: it loads into stock CPython only with `LD_PRELOAD=$(gcc -print-file-name=libasan.so)` and `ASAN_OPTIONS=detect_leaks=0`; it needs `PYTHONMALLOC=malloc` (otherwise `regex`'s small blocks come from pymalloc arenas, which have no redzones); `python -I` ignores `PYTHONMALLOC`, so the children must not use `-I`; and the job starts with a positive control, an out-of-bounds `memset` that must be reported. Revision 6's run on the 12 Nov host: 1,800 s × 2 workers (1 worker-hour) on the revision-6 translator: 287,725 patterns, 267,183 compiled and matched (114.6 million matches, 3,190 timeouts at 0.2 s), 20,510 invalid in `re` and 32 refused by the translator, skipped; **0 child deaths, 0 ASAN/UBSan reports, 0 errors**; the positive control was reported (`clock/results/asan-fuzz-host2/`);
  - only patterns that pass the publish limits reach `regex`, which keeps the reachable surface to the shapes the fuzz covers.
- **The pin is frozen, not just old.** It cannot follow upstream without losing exact `\w`.
  - **Exit plan:** explicit code-point sets built from `re` (749 ranges for `\w`, 64 for `\d`), exact on any `regex` release but 6–14× slower. If a security fix must come from a later release, the translator switches to explicit sets, the per-pass budget absorbs the slowdown, and the clock patch is re-applied to that release (its two sites are unchanged in 2026.11.9). §9 records this.

#### 5.2.3 Publish-time limits and where they are checked

- **The limits.** Revision 6 adds the last four rows (B-2, B-4 and a new finding):

  | Limit | Value | Why |
  |---|---|---|
  | Pattern length | ≤ 1000 | today's limit |
  | Each counted repetition, and the product of nested counted repetitions | ≤ 1000 | revision 4 |
  | Regex rules per plan | ≤ 64 (`_MAX_REGEX_RULES_PER_PLAN`) | revision 4 |
  | Total expanded repetition | ≤ 1000 per pattern | revision 5 |
  | Distinct case-insensitive items | ≤ 64 per pattern; ≤ 512 per plan | revision 5; the per-plan sum is now enforced by `check_plan` in all three callers |
  | **Group nesting depth** | ≤ 16, by an iterative scan **before** `re._parser` | B-2: 499 levels (999 characters, no repeats) raised `RecursionError` in `re`, in `check_limits`, `translate` and `stopper`, and at HEAD in the projector |
  | **Translated size** (parse-only estimate) | ≤ 16,384 per rule; ≤ 131,072 per plan | B-4: `\s`×500 became 39,000 characters (compile 53–85 ms); `\b.`×330 became 27,060 (82–113 ms) |
  | **Compile units** (parse-only estimate) | ≤ 32,768 per rule; ≤ 262,144 per plan | new: unrolled repeat minimums (§5.2.1). Bounds the one GIL-holding C call |
  | **Translation CPU** (gateway only) | ≤ 50 ms per rule | A-B1 safety net (§5.2.1) |

- **Why 16 and not 32.** The limit must keep `regex.compile` of every accepted pattern clear of Python's default recursion limit (1000) even from a deep caller. Measured frames per level of source nesting, worst shape `(?i:a|…)*+` (four groups per level once translated): `re._parser` 2, `translate` 6, **`regex.compile` 18**. At 32 levels `regex.compile` of that shape needs 586 frames and raised `RecursionError` from a 600-frame stack; at 16 it needs about 300, and all 8 worst shapes plus 204 random 16-deep patterns compiled from 600 frames deep. Real patterns are far shallower: the deepest string literal in the whole repository is 5 levels (PR #117's census, 254,755 literals), the fuzz grammar nests 3 and the performance corpus 2.
  - **Coordination with PR #117** (the live projector hotfix): #117 set `MAX_GROUP_DEPTH = 32` at HEAD, for `re`. Both of #117's reviewers recommend **16** to match this cap, and so does this plan, so that no rule saved between #117 and F1a-1 can become a refused rule when F1a-1 ships. If #117 keeps 32, rules 17–32 deep saved meanwhile are refused by v2 (not by v1) and fail closed in v2 with `policy_regex_timeout`, counted in `refused_conditions`, as any stored rule over a limit is (below); the F1a-1 live check lists them. Coordinator decision; F1a-1 code uses 16 either way.
- **The estimate.** Translated size and compile units come from the parse-only check, not from translation. `analyze` runs the translator's own emission walk in **estimate mode**: every construct is emitted exactly as in translation, and the one step that needs `regex`, the case-insensitive patch, is replaced by padding of its proven maximum (§5.2.1). `translated_size` is the length of that text; `compile_units` is each character multiplied by the product of the copies `regex` builds for the repeats around it (min, plus 1 when max > min; 1 when min is 0). Real ≤ estimate on 9,744 accepted fuzz patterns (3 seeds × 3,400; 456 refused by a limit) fuzz patterns, for both numbers, with 0 below; median ratio 1.00 (exact for case-sensitive patterns), p99 2.80–2.87, max 3.55 for size and 3.65 for units, the largest being patterns with many case-insensitive items; 0 patterns accepted that `re.compile` refuses; 0 violations of the patch bound on 10,200 random sets (`estimate_test.py`, `r6_estimate.txt`). The gateway also checks the real translation against the estimate before compiling; a breach is refused as a translator bug.
- **One parse-only check, in three places.** `check_limits` (per rule) and `check_plan` (per plan) run the depth scan, parse with `re._parser`, walk the tree for the refusal rules and limits, and run the estimate walk. They never import `regex`. Any exception becomes a refusal. `check_plan` checks each rule, then the per-plan sums, and refuses rules **from the last one back** until the sums fit, so every caller refuses the same rules. It is used by:
  - control at save, as a **non-blocking v2 verdict** (a byte-identical copy in `ai_mesh_shared/regex_limits.py`): the save succeeds, as v1 needs (§4, D6), and the response and console carry "refused by the v2 gateway: <reason>". Django admin and `load_policies` get the verdict from the projector check;
  - the projector's `validate_plan`;
  - the gateway, before translating.

  A CI test asserts that the copies are identical, that the three callers agree on the fuzz corpus, that real ≤ estimate on it, and that the check refuses every pattern `re.compile` refuses.
- **Measured cost at the limits** (`ev6/perf6.md`, 12 Nov host, re-run under the heavy lock). It replaces revision 5's table, whose "236 ms per plan" and "9.3 ms worst translation" missed the shapes above:

  | Worst accepted shape at each limit | Translated (units) | Translate | Compile (of which the GIL-holding C call) | Retained / peak | Match, 32 KiB prose (`re`) |
  |---|---|---|---|---|---|
  | `\s` repeated, at the size cap | 16,380 (16,380) | 0.3 ms | 19.0 ms (0.15) | 165 / 612 KiB | 0.9 ms (0.3) |
  | `\b.` repeated (two-sided boundary), at the size cap | 16,318 (16,318) | 0.5 ms | 40.5 ms (0.64) | 847 / 1,899 KiB | 13.0 ms (0.7) |
  | `\b` repeated (two-sided, consecutive), at the size cap | 16,372 (16,372) | 0.4 ms | 39.9 ms (0.74) | 1,126 / 2,337 KiB | 52.4 ms (26.9) |
  | case-insensitive keyword list | 7,239 (7,239) | 3.6 ms | 20.8 ms (0.15) | 92 / 309 KiB | 39.1 ms (8.8) |
  | 64 distinct case-insensitive 2-character sets | 1,203 (1,203) | **16.9 ms** | 3.2 ms (0.03) | 19 / 65 KiB | 6.7 ms (0.3) |
  | `\w{3}\s`-style counted group, at the units cap | 113 (31,250) | 0.1 ms | 0.9 ms (0.42) | 734 / 924 KiB | 11.1 ms (1.5) |
  | `(a)+` nested, at the units cap | 81 (32,756) | 0.1 ms | 3.0 ms (**2.34**) | **1,897 / 2,956 KiB** | 10.0 ms (8.2) |
  | `(?i:a\|…)*+` nested 16 (the stack worst) | 477 (461) | 0.7 ms | 2.3 ms (0.04) | 24 / 55 KiB | 184.7 ms (29.2) |

  | Plan of 64 rules at the per-plan limits | Accepted / refused by the sums | Translate | Compile | Largest C call |
  |---|---|---|---|---|
  | `\b.` at the size cap (the costliest per character) | 8 / 56 | 4 ms | **332 ms** | 1.06 ms |
  | `(a)+` nested at the units cap | 8 / 56 | 2 ms | 32 ms | **4.07 ms** |
  | `x{1000}` at the units cap | 64 / 0 | 4 ms | 16 ms | 0.22 ms |
  | 64 distinct case-insensitive sets per rule (512 per-plan items) | 8 / 56 | 134 ms | 26 ms | 0.03 ms |

  Thread CPU, 12 Nov host, load average 3.4, best of 3 per shape (`perf6.md`, `r6_perf6.log`; the 11 Nov run at load 4.6–7 gave the same ranking within 1.6×). So a plan costs at most about 0.33–0.55 s of compile CPU per worker (332 ms here; 335–545 ms on 11 Nov), against Reviewer B's 7 s for revision 5's limits, and no single GIL-holding call exceeds about 4 ms.

  Reviewer B's revision-5 cases against revision 6: `\s`×500 and `\b.`×330 are refused by translated size; `(?=\w)`×166 + `a` and `(?<=\w)`×142 + `a` stay accepted (match cost charged to the org, 15.9 ms on 32 KiB of prose against `re`'s 50.0 ms); the 400- and 499-deep bombs are refused by depth; `(a)+` nested 16 is refused by compile units (1,048,564 against 32,768); deeper nests are refused by depth.
- **The projector's safety path.** Today `desired_state` runs `validate_plan` before `mark_projected` writes the safety heartbeat (`projector.py:209,224`). F1a-1:
  - runs only the parse-only check, never translation or `regex`: 300 ms for the worst hostile 64-rule plan (1000-character `\b`-dense case-insensitive rules; 9.7 ms per rule at worst), 166 ms for 64 990-character literal alternations, against revision 5's 59–88 ms for the same plans: the estimate walk costs 2–5×, inside the 4 s round box and memoised by body hash. The nesting bombs are refused before parsing (64 bombs: under 1 ms);
  - catches **every** exception per rule and counts the rule as refused, so nothing escapes the round (requirement 7). At HEAD the 499-deep row raises `RecursionError` out of `round_once` (`validate_plan` catches only `ValueError`, `UnicodeEncodeError` and `CompileError`, `projector.py:565,578`; `_prepare` catches only `re.error`, `conditions.py:108-111`), the heartbeat ages, and after `AMF_PROJECTOR_LAG_MS` every gateway fails closed. PR #117 is that hotfix at HEAD and merges before F1a-1;
  - memoises validation by body hash and moves it out of `desired_state` into `_plans`, with a per-round time budget of a quarter of `lag_ms`. A plan not validated within the budget keeps its previous record and is validated in a later round, with a note. With the per-plan caps above (64 rules, translated size, compile units, case-insensitive items; pattern length 1000 in the gateway too), this is issue #126's fix: a round with no plan changes does no per-org validation work;
  - **never runs a customer regex (issue #125).** At HEAD, a REWRITE rule's `check_template` (`rewrite_template.py:91-98`) runs the tenant's pattern inside `validate_plan` every round, with a 0.25 s thread-join "budget" that cannot fire while `re` holds the GIL: a 27-character template cost 12.4 s of CPU, lost the projector lease and failed the fleet closed. In F1a-1 the projector keeps only the parse-only verdict; the template check runs in control at save (as the non-blocking v2 verdict, §4) and in the gateway on the scan scheduler with the patched engine, where its budget is real (§5.2.2: a timeout fires at the scanning thread's own CPU, within +0.5 ms, while the GIL is released);
  - **validates oldest-pending-first (Reviewer B, revision 5, NIT 7):** by the time the org's current unvalidated body was first observed, ties by slug. A newer body from the same org replaces the pending one but keeps its timestamp, so re-publishing every round neither loses an org its place nor jumps the queue; each org has at most one pending body. A pending org waits at most ⌈(orgs pending ahead × worst plan check) / time box⌉ rounds;
  - tests: a hostile 64-rule plan re-published with a new body every round keeps the heartbeat inside `lag_ms`; a plan holding a 499-deep row keeps the heartbeat fresh and refuses that rule; 20 orgs publishing in one round in reverse slug order are validated in first-seen order.
- **Breaking a limit:** control warns at save (non-blocking during the revamp, §4). In v2, a rule over a limit, new or stored, is a refused rule, which fails closed with `policy_regex_timeout` on an applicable request, as today, and is counted in the projector's `refused_conditions`. v1 keeps compiling the same rule with `re`, unchanged.
- **Gateway compile.**
  - Translation and compile are lazy: they run on the first request that needs the org's program, on the scan scheduler (§5.2.5), off the loop, and charged to that org. They are **single-flight** per `(org, plan version)`.
  - `compile_rule` is the one gateway entry point: limits, translation under its CPU cap, the real-against-estimate check, then `regex.compile`. Any exception is a refused rule.
  - **Loop stall.** Each rule is its own step, with a GIL yield between rules. The Python part of translation and compile is interrupted at every switch interval like any thread. The one part that cannot be interrupted is the C call `_regex.compile`, and the compile-units cap bounds it: ≤ 4.07 ms in the plan runs and ≤ 2.34 ms in the shape table (12 Nov host) measured at the limits, about 5 ms by the worst measured cost per unit (124–158 ns), which is the switch interval. So a compile step stalls the loop no longer than a busy pure-Python thread does. The bound is stated that way, against a pure-Python baseline in the same run, not as an absolute figure: revision 5's 7.3 ms and Reviewer B's 9–15 ms are the same quantity at different host loads. Measured (`stall6.py`, 12 Nov host): a plan at the limits (12 rules accepted by `check_plan`, 371 ms of translate + compile CPU) compiled rule by rule with a yield: loop lag p99 14.8 ms, max 15.7 ms (median of 3 rounds; worst round 23.5 ms); a busy pure-Python thread of the same duration in the same process: p99 13.2 ms, max 11.8 ms (worst round 17.3 ms); load average 3.0–3.6, with other agents' review harnesses running (`r6_stall.txt`).
  - **The per-worker program cache is bounded by bytes.** Each entry is charged `80 × compile_units + 16 KiB`, known at publish (the projector already has it). It covers every measured shape: retained bytes are 10–70 per unit, the worst rule at the limits retains about 1.9 MiB (2.9 MiB peak), ordinary rules under 20 KiB, and a plan at most about 21 MiB. Eviction is metered.
- **Other `re` call sites.**
  - `rewrite_template.py:91-98` (the REWRITE template check): goes through the same engine, on the scan scheduler and never on the projector or the applier (issue #125). Its literal `_CHECK_BUDGET_S = 0.25`, a thread-join timeout that could not fire while `re` held the GIL, is replaced by `guard_wait_budget_s()` of the scanning thread's CPU. A timeout refuses the rule, as any refused rule.
  - Control's `_matches_own_pattern` (`firewall_v2_publish.py:586-593`): gated by the parse-only check and `_has_redos_shape` first, and run off the request thread with a bound.
- **What does not change.** `_redos_shape` keeps refusing the shapes it refuses today, and the timeout posture is unchanged.

#### 5.2.4 Matching, budgets and the streaming scan

- **The budget unit is the scanning thread's CPU time** (with the patched clock, §5.2.2). Every budget, bucket and bound below is in thread CPU, debited with `time.thread_time()` around each call. Revision 5's statement that stock `regex` already counts thread CPU was wrong (B-1, B-6): revision 4's wall-time "overshoot" was host contention, but stock's timeout is process CPU.
- **Calls.** `compiled.finditer(text, pos=q, timeout=t, concurrent=True)`. Before calling, `t` must be finite and > 0; otherwise the scan refuses without calling `regex` (negative means no timeout in `regex`; on a stock build NaN also means no limit and anything ≥ about 9.2e12 s fires at once; the patch fixes those two, the guard stays). A timed call or iterator is created and advanced on the same scan thread; the patch fails closed if it is not.
- **The per-pass budget scales with the text.** An off-loop pass gets `guard_wait_budget_s() × max(1, len(text) / 128 KiB)`, shared by all of its rules: 100 ms up to 128 KiB, and 400 ms at the 512 KiB output ceiling. Non-streaming scans use the same formula. A timeout of a final pass refuses the request with `policy_regex_timeout`, as today.
- **Headroom: thread CPU inflates under host contention (new).** The thread CPU of one fixed pass (B's 3 rules over 128 KiB, single thread, no timeout) varied max/min 1.7× on the shared host: 11 Nov, two series of 60: p50 56.8 and 67.8 ms, max 80.6 and 82.1 ms; 12 Nov: max/min 1.24 and 1.48 (p50 54.2 and 54.7 ms, max 66.5 and 79.0 ms) on a quieter host; the design takes the larger, 1.7×. That is shared-cache, SMT and frequency contention, which thread CPU cannot remove (steal time was about 1%). So the per-pass budget is a refusal threshold for catastrophic patterns, not a cost estimate. Measured with the patch: at about 57% of the budget, 0/80 refusals; at about 80%, 8/80, and the rate did not depend on org A (2/20 with B alone): B's own variance. Therefore:
  - §5.8's performance gate asserts that each gate-corpus plan's pass over 128 KiB costs at most **50%** of its per-pass budget on the CI runner;
  - the gateway exports `amf_regex_pass_budget_fraction` (a histogram, no org label) and logs the org and rule when a pass uses more than half its budget, so a plan near its budget is visible before it is refused.
- **The GIL convoy: a pass's wall time is not bounded by its CPU budget (new).** `concurrent=True` releases the GIL for the match, but `safe_check_cancel` (`_regex.c:2221-2234`) takes it back every 256 matcher iterations, with or without a timeout, to call `PyErr_CheckSignals()`. So an off-loop scan waits for the GIL whenever the event loop is running Python.
  - **It cannot cause a refusal or cross-tenant effect.** The wait accrues no thread CPU (M's own CPU stayed 54–83 ms while its wall grew; stock counted the waiting as M's and refused it). Budgets and refusals are in thread CPU, so the convoy adds only wall latency, to the stream or request that awaits the pass.
  - **Measured** (patched wheel, one translated rule over 384 KiB, `concurrent=True`; thread H stands in for the loop). Loop 100% busy in Python: wall ×12 at the default 5 ms switch interval, ×3.6 at 1 ms (11 Nov host). By the loop's Python duty cycle (12 Nov host, `gil_duty.py`, median of 5): 

    | Loop's Python duty cycle (H, 10 ms period) | wall / CPU, 5 ms switch interval | wall / CPU, 1 ms |
    |---|---|---|
    | 0% | 1.0 | 1.0 |
    | 25% | 1.3 | 1.4 |
    | 50% | 1.7 | 2.0 |
    | 75% | 2.7 | 3.6 |
    | 90% | 3.2 | 6.9 |
    | 100% | 3.2 | 5.8 |

    M's own CPU stayed 54–77 ms in every row. A fully busy loop (`gil_probe.py`) gave ×3.9 at 5 ms and ×2.4 at 1 ms on 12 Nov, against ×12 and ×3.6 on 11 Nov: the factor depends on the host as well, so the plan uses these as measurements, not a bound, and a smaller switch interval is not a reliable lever.
  - **What bounds it in production.** The loop's customer-regex share is capped by the worker-wide inline bucket (25%, §5.2.5); the rest of the loop's Python is request handling, bounded by admission and the contract's utilisation cap. The plan states the off-loop wall latency as the pass's CPU times the measured factor at the loop's duty cycle, not as a bound in CPU alone.
  - **Observability.** `amf_regex_pass_cpu_seconds` and `amf_regex_pass_wall_seconds` histograms (no org label); their ratio is the convoy factor, and F5 load tests record it.
  - **Not in F1a-1; prototyped and tracked (§9).** A second patch skips the GIL in the periodic check on non-main threads, where CPython never handles signals, and takes it only to raise (`ev6/clock/regex-2023.8.8-nogil-check.prototype.patch`, +53 lines, a prototype build only). Measured on the 12 Nov host: wall / CPU 1.0–1.5× at every duty cycle and switch interval (the same table: 1.0, 1.0, 1.1, 1.5, 1.5, 1.2 at 5 ms); in the cross-tenant test B's wall per pass halves (T = 8: 257 ms against 540 ms) and B is still refused 0/80; upstream's 100 tests pass; timeouts fire at 50.1 ms of own CPU on the main and on a worker thread; a migrated timed iterator still fails closed; a signal still interrupts a main-thread match (`clock/results/host2_*nogil*`, `host2_semantics.txt`). It restores the parallelism `concurrent=True` was meant to give, but it is a second fork delta, and the convoy affects neither correctness nor isolation. So F1a-1 ships the clock patch alone, and the second patch, or the process fallback (§5.2.2), is decided at F5 with load measurements, with the same evidence bar as the clock patch (exactness, crash fuzz and ASAN on the two-patch wheel).
- **The streaming scan is incremental.** Each rule's hit list stays **identical** to a whole-text rescan after every delta, while scanning far less.
  - **The idea.** An attempt at position t whose outcome can no longer change is *final*. The scanner keeps a resume point q before which every match and every failed attempt is final, and the next scan resumes at q with `finditer(text, pos=q)`. `pos` keeps the earlier text visible to lookbehinds, and `^`/`\A` do not match at q, exactly as in a whole-text scan.
  - **Two exact finality rules,** from the parse tree: **reach** (an attempt at t is final once t + R ≤ len(text), R finite unless the pattern has `*`, `+`, `{n,}`, a backreference, or a lookbehind that reads to its right) and **stoppers** (characters no consuming item can match; the attempt at t is final once any stopper follows t). Patterns with `\Z` or a non-multiline `$` have no stoppers.
  - **Fallback.** A rule that can match empty, or that has neither a finite reach nor a stopper (`(?s)a.*b`), keeps the whole-text rescan.
  - **State.** Final hits that end before the released offset are dropped, so the state per stream stays O(R + held text).
  - **Evidence.** 0 mismatches over 39,998 incremental streams and 655,969 deltas (revision 5); Reviewer A (18,284 harsher streams) and Reviewer B (36,000 streams, 764,482 deltas, 40 hostile patterns) found none either. `incremental.py`'s `scan()` is unchanged in revision 6; revision 6 adds `scan_or_hold` (below), and the re-run on seeds 11–13 is in §5.2.1's table.
- **The streaming budget.** Each delta's inline customer scan gets one budget of **half a scan window** of thread CPU (5 ms at the default p99), shared by all its rules.
  - **No refusal inline.** When a rule's inline call times out, or the inline budget is spent, the rest of that delta's customer scan **moves off the loop** as one pass, under the per-pass budget. The stream awaits that pass before releasing those bytes. A whole-text-rescan rule that moved off the loop stays off the loop for the rest of that stream.
  - **Coalescing.** A stream has at most one off-loop pass in flight; deltas that arrive meanwhile are scanned together by the next pass. The released bytes do not depend on how deltas are grouped (Property 4), and the existing property test runs over the coalescing layer.
- **Timeouts on non-final passes: hold and retry (Reviewer A, revision 5, S1).** Pattern cost need not grow with the text: `^(\w+\s?){1,30}[.!?]` passes `_redos_shape` and every limit, times out on the first six words of A's quarterly-report sentence (no terminator yet), and matches the whole sentence in 0.04–0.10 ms. Revision 5 refused the stream there, which broke requirement 4. Revision 6 (`incremental.py` `scan_or_hold`):
  - a timeout on a pass that is **not** the final one leaves the rule **undecided** at its resume point q. The stream releases nothing at or after q; everything before q is final and decided;
  - the pass is retried when a stopper arrives after the failed pass (at most once per growth step), when the text has doubled since the failed pass, or at the end of the stream. State is unchanged by a timeout, so a retry resumes at the same q;
  - **only the final pass refuses.** It resumes at q and does a subset of the whole-text scan's attempts under the whole text's budget, so it refuses only where one non-streaming scan of the same text would;
  - **a dedicated undecided-hold cap of 4 KiB.** The existing holdback cap (`hold_cap_tokens`, default 3 tokens, ceiling 6, `runtime/holdback_config.py:22`) is far too small (A's sentence needs about 28 tokens). Over 4 KiB of undecided text the stream is refused with `policy_regex_timeout` and the same audit reason;
  - **cost:** only on streams where a pass times out. With "once", at most 2·log2(4096 / L₀) + 1 timed-out passes per rule per stream (L₀: the length at the first timeout); for A's case (L₀ = 28) that is ≤ 15 passes, at most 1.5 s of the org's own scheduler share at 100 ms. "Always" (retry on every stopper) was rejected: in prose with commas it spends one budget per comma on a rule that stays expensive;
  - **measured** (`s1_hold.py`, `s1_test.py`, 12 Nov host): A's sentence (110 characters in 28 deltas, 100 ms budget, 4 runs): "refuse" refuses at 28 characters; hold-once decides with the whole-text hit list `[(0, 110)]` after 2 timed-out passes (200 ms of the org's CPU), holding up to 108 characters over 21 deltas. With a comma before the terminator the whole text is refused once non-streaming, and hold-once refuses at the end (4 timed-out passes, 400 ms). 8 KiB of words with no stopper and no terminator: hold-once refuses at the 4 KiB cap (4,112 characters, 8 timed-out passes, 800 ms). On the incremental fuzz corpus (4 runs, 6,497 streams, the per-pass budget scaled down to 1 and 10 ms so that passes time out): "refuse" refused **132 streams whose whole text is not refused**; hold-once refused **0** such, and exactly the 139 streams the whole-text scan refuses; every decided hit list equalled a whole-text `finditer` (0 mismatches); held text per stream median 46–50 and at most 150 characters (at most 25 consecutive deltas); CPU on the streams where a pass timed out 1.5–1.6× that of "refuse".
- **Requirement 4, therefore:** a stream is refused for regex cost only where the same text, scanned once non-streaming, would be refused, or where a rule stays undecided over 4 KiB of held text.
- **Measured per-delta cost** (10 rules, thread CPU, per 4-character delta at the given output length; revision 5, unchanged: `scan()` did not change). Rule set B is Reviewer B's (IBAN shape included); rule set A is Reviewer A's (`\b\w+\s+(?:token|secret)\b` included). "Unpunctuated" is words and spaces only: no stopper ever appears.

  | Rules | Text | 32 KiB: rev. 4 → rev. 5 | 128 KiB | 512 KiB |
  |---|---|---|---|---|
  | B | prose | 0.71 → 0.04 ms | 2.50 → 0.04 ms | 11.1 → 0.04 ms |
  | B | code | 1.29 → 0.04 ms | 5.04 → 0.04 ms | 20.2 → 0.04 ms |
  | B | unpunctuated | 0.73 → 0.07 ms | 2.79 → 0.18 ms | 11.3 → 0.57 ms |
  | A | prose | 4.83 → 0.11 ms | 37.5 → 0.06 ms | 88.3 → 0.04 ms |
  | A | code | 5.35 → 0.04 ms | 19.5 → 0.04 ms | 80.5 → 0.04 ms |
  | A | unpunctuated | 6.40 → 4.30 ms | 21.0 → 18.7 ms | 85.3 → 78.7 ms |

  The worst case, set A on unpunctuated text, crosses the 5 ms inline budget at about 32 KiB, moves off the loop and coalesces; a pass at the 512 KiB ceiling takes about 79 ms against a 400 ms budget (about 20%, inside the headroom above). Reviewer B reproduced these (0.04 / 0.07 ms per delta at 32 / 128 KiB; 4.1 → 14.9 ms for one 128 KiB word).

#### 5.2.5 Tenant isolation: the inline buckets and the scan scheduler

- **Inline share.** Two buckets of loop thread CPU, both debited with each inline customer call's measured thread CPU:
  - **per org,** per worker: rate ρ = 25% of one core, burst one scan window (revision 5);
  - **worker-wide (new, Reviewer B, revision 5, should-fix 3):** rate 25% of one core, burst one scan window (10 ms). B measured that per-org buckets alone let 4 busy orgs take 98% of the loop.
  - An inline call runs only when **both** buckets hold credit; otherwise that delta's customer scan goes off the loop. Nothing is refused.
  - **Refill by the loop's available time,** its thread CPU plus the time it spends idle in the selector, not by wall time: on an oversubscribed host an inline call stretches in wall time, and a wall-clock bucket would refill during the very calls it limits.
  - **Bound.** With debits after each call of at most c = 5 ms (the inline budget), a run of k consecutive inline calls refills ρ·k·c while spending k·c, so k < burst / (c·(1 − ρ)) + 1, and a tenant-B callback waits behind at most **burst / (1 − ρ) + c = 18.3 ms of loop CPU** (burst 10 ms, ρ = 25%).
  - **Measured** (`inline_bucket6.py`: B's simulation with a per-org bucket of 25%, K busy orgs × 4 streams × 50 deltas/s, each delta about 4.5–5 ms of CPU, just under the inline budget; tenant B's 1 ms sleeper; median of 3 runs of 3 s; 12 Nov host under the heavy lock): 

    | Worker-wide bucket (12 Nov, same run) | Busy orgs | Inline share of the loop | B wall delay p50 / p99 | Loop CPU ahead of B p50 / p99 / max |
    |---|---|---|---|---|
    | none (revision 5) | 4 | 84% | 53.3 / 107.5 ms | 50.1 / 71.8 / 94.1 ms |
    | 25%, refilled by wall time (B's simulation) | 2 / 4 / 6 | 24% / 25% / 25% | 0.2–0.6 / 23.5–62.4 ms | 0.1 / 10.5–18.4 / 27.2–30.4 ms |
    | **25%, refilled by loop-available time (the design)** | 2 / 4 / 6 | 24% / 20% / 20% | 0.2 / 9.4–15.2 ms | 0.1 / **5.8–7.5** / **11.0–14.2 ms** |
    | 0% (every scan off the loop) | 4 | 0% | 0.1 / 6.1 ms | 0.05 / 0.19 / 9.5 ms |

    With the design's refill, B's lateness in loop CPU stays inside the 18.3 ms bound at its maximum; refilled by wall time, the max (27–30 ms) does not, because a call stretched by host preemption earns credit (`r6_inline_avail.txt`; `r6_inline_locked.txt` has the 25%/40%/50% sweep with wall refill). 40% and 50% admit more inline work and give B proportionally more delay without a benefit the plan needs, so 25% is chosen. A lag gate (skip inline scans while the loop's last lateness exceeds 5 ms) on top of a wall-refilled 25% bucket gave about the same p99 of loop CPU ahead of B (7.7–18.7 ms against 11.9–24.9 ms, 12 Nov) and a worse max at 4 orgs (44 ms), so it is not added; the available-time refill is what holds the bound.
- **Off-loop passes run on a scan scheduler,** not directly on the shared executor (Reviewer B measured tenant A pushing tenant B's off-loop scans to p99 1.4–2.1 s on the shared executor).
  - The scheduler has T scan threads, contract-derived from the core count.
  - Each org has its own FIFO, served by deficit round robin weighted by each pass's measured thread CPU, so under contention every active org gets an equal CPU share.
  - An org uses at most max(1, T − 1) threads at a time, so one org can never hold every thread.
  - The queue depth is bounded by admission: each admitted request has at most one pass queued or running (streams coalesce). So the scheduler adds no new shed path, and admission's existing concurrency cap bounds it.
  - **Bound (made explicit, B-3).** Let A be the other active orgs, b the largest per-pass budget among the passes ahead, and the DRR quantum Q = b. A tenant-B pass waits for the passes running when it arrives (at most b each, on T threads), then for one DRR round, in which each org runs less than Q + b (DRR debits after the pass, from its measured CPU):

    **wait ≤ (T·b + A·(Q + b)) / T = (T + 2A)·b / T**, which for T = 2 is (active orgs including B) × (per-pass budget).

    b is set by the **other** orgs' pass sizes: 100 ms for passes up to 128 KiB and 400 ms at the 512 KiB ceiling. A discrete-event simulation (`drr_bound.py`, `r6_drr.txt`) gives about half the bound as the worst case:

    | T | Active orgs (incl. B) | b = 100 ms: simulated worst / bound | b = 400 ms: simulated worst / bound |
    |---|---|---|---|
    | 2 | 2 | 0 / 200 ms | 0 / 800 ms |
    | 2 | 4 | 200 / 400 ms | 799 / 1,600 ms |
    | 2 | 8 | 398 / 800 ms | 1,592 / 3,200 ms |
    | 2 | 16 | 780 / 1,600 ms | 3,118 / 6,400 ms |
    | 2 | 32 | 1,475 / 3,200 ms | 5,898 / 12,800 ms |
    | 4 | 8 | 200 / 450 ms | 799 / 1,800 ms |
    | 4 | 32 | 750 / 1,650 ms | 2,999 / 6,600 ms |

  - **Units.** The bound is thread CPU per scan thread. It equals wall time only when each scan thread has a core and the loop is not holding the GIL: with more scan threads than cores multiply by T / cores, and add the convoy factor of §5.2.4.
  - **This is a capacity statement.** At 16 active orgs with 512 KiB passes, B can wait 3–6 s of scan CPU. The levers are admission's per-org concurrency cap and the number of scan threads; §9 says so, and F5 measures it.
- **Every other customer scan goes through the scheduler too:**
  - chat input (`openai_chat.py:300`);
  - the non-streaming whole-response scan (`openai_nonstream.py:176-177`);
  - MCP input and replies (`mcp.py` `_forward`);
  - embeddings (`openai_misc.py:445`);
  - moderations, RAG and the error frame;
  - **STRICT_WITHHOLD.** `build_output_detectors` returns `runner=None` when a plan has no built-in output families (`output_detectors.py:162-163`), so `strict.py:439-440` scans the whole withheld output on the loop. The scheduler is now always passed as the runner;
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
   - **Reserved slugs (Reviewer B, revision 3 NIT 11).** The audit ids `_calibration` and `_unauthenticated` are also valid org slugs today, the same class of collision as the lease key. Revision 6 (D6): control does not refuse such slugs, because v1 shares control's org model. On the v2 side, the projector does not project an org whose slug starts with `_` (a declared note, counted), so v2 never serves it and its audit stream cannot collide with `_calibration` or `_unauthenticated`; control lists any such org in a non-blocking check. Refusing them at control is a post-revamp item.
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
  - **Semantics against `re`** (all in CI, on the pinned pair, **on the built patched wheel**):
    - the exhaustive per-code-point class and boundary tests, including the one-sided boundaries (`exhaustive_boundary.py`), every case-insensitive single literal, and every pair and triple of single-character items in an alternation (`alt_exhaustive.py`);
    - **every spelling of a one-code-point negation** in alternations (`alt_negated.py`: `[^9]`, `[^9-9]`, `(?:[^9])`, `(?:(?:[^9]))`, `([^9])`, astral and astral-range forms, `[^99]`, `[^k]`, `[^K]`, `[^ı]`; pairs and triples; plain, repeated, in a negative lookahead, after a literal and in a lookbehind; no flag, `(?i)`, `(?s)`), plus the check that no translation contains a one-code-point negated set;
    - **the range classifier** against a brute-force `re` classification over every code point of each range (`range_b1.py` part 1), and `gen_cased.py --check` for `WORD_FLIPS`, `CASED_RANGES` and `FOLD_DISAGREE`;
    - **the estimate:** real translated size and compile units ≤ the parse-only estimate on the fuzz corpus, and the case-insensitive patch points ⊆ the bounded set on every cased literal and 20,000 random sets (`estimate_test.py`); the parse-only check refuses every pattern `re.compile` refuses;
    - the differential fuzz (possessive repeats, atomic groups, lookbehinds, single-character alternations), in worker processes with a per-pattern timeout;
    - the incremental streaming fuzz: after every delta, the incremental hit list equals a whole-text `finditer`, for both finality rules;
    - the reviewers' bypass cases: U+001F separators in a card pattern, U+001C in `password\s*=`, a combining mark after `\bpassword\b`, `(?i)\bssn\b` against `ssǹ`, `(?:a+x?){2,}+` on `aaa`, `(?:[^9]|[^c])`, **`(?:[^a-a]|[^b-b])`** and `(?i)[\U0001e91c\x41]` against U+1E91C (`re`'s verdict: no match);
    - the `SystemError` regression: `x(?:(a)a{1,2}|aba(?i:A)|x?x?){2,}+` is refused at publish and fails closed at the gateway;
    - the boot check, including the full functional comparison and the patched-build markers (extension sha256, NaN → `ValueError`).
  - **The patched wheel (B-1, B-5):**
    - the build job rebuilds the wheel from the hash-pinned sdist, patch and backend, and fails unless the wheel's sha256 equals the recorded one for that architecture, and unless the compile line has `-O2` and `-DNDEBUG`;
    - upstream's `regex.test_regex` passes on the built wheel;
    - **the cross-tenant test** (`cross_tenant.py`): org B's 3-rule pass over 128 KiB at about 57% of its budget is not refused while T − 1 threads of org A burn pathological passes, for T = 2 and 4; org A's passes are refused at their own budget ±1 ms of their thread CPU;
    - a timed iterator advanced on another thread fails closed; NaN raises `ValueError`; `inf` does not fire at once.
  - **Performance gate.** On a fixed corpus (prose and code-like text, 32 KiB, including the `\b`+class family), each translated rule's thread CPU stays within 6× `re`'s, with each side floored at 0.05 ms. Revision 6 adds case-insensitive keyword lists and possessive nests with their own allowed ratio, 10× (measured 4–10×), until the prefilter guard is narrowed. **New (headroom):** each corpus plan's pass over 128 KiB costs at most 50% of its per-pass budget on the CI runner.
  - **Crash fuzz.** Translator output plus the limit-edge shapes is compiled and matched in child processes on the patched wheel. Any death by a signal fails the job. **ASAN + UBSan** runs the same corpus weekly and on PRs that touch the patch, the pin or the translator, with the positive control first.
  - **Regex DoS:**
    - patterns that pass `_redos_shape` and the limits, yet time out under `regex`. Example: `(\w+\s?){1,30}$`;
    - **the nested-repeat compile bomb** (`(a)+` nested 16) is refused at publish; no accepted pattern's `_regex.compile` call exceeds the measured bound (units cap × the worst cost per unit);
    - **A's range pattern** (166 distinct wide ranges, and its `\b` form) translates within the measured bound, and a rule whose translation exceeds the 50 ms CPU cap is refused with reason `translate_cpu`;
    - **two-tenant tests,** each asserting tenant B's per-delta loop delay and off-loop wait against the §5.2.5 bounds, in thread CPU:
      - (a) tenant A has R rules, each tuned just under the inline budget, on the non-strict streaming path, with C concurrent streams that are never refused. A's inline share stays within ρ plus one burst;
      - (a2) **K busy orgs** of that shape (K = 2, 4, 6): the aggregate inline share stays within the worker-wide rate plus one burst, and B's lateness in loop CPU within 18.3 ms;
      - (b) tenant A has C concurrent pathological streams. Each moves off the loop, then is refused after one per-pass budget;
      - (c) the same as (b), on the chat input path and on STRICT_WITHHOLD;
      - (d) tenant A floods the scan scheduler with pathological non-streaming requests at the admission limit. Tenant B's off-loop pass waits within (T + 2A)·b / T;
    - negative, zero and non-finite remaining time never reaches `regex`.
  - **No false refusals:**
    - a 32k-token stream of prose and a 16 KiB code-like stream complete, each with 10 ordinary rules including the IBAN shape and `\b\w+\s+(?:token|secret)\b`;
    - a stream at the 512 KiB output ceiling with Reviewer A's 10 rules completes, on unpunctuated text too;
    - a stream whose whole-text-rescan rule outgrows the inline budget moves off the loop and completes, releasing the same bytes as a non-coalesced run;
    - the per-pass budget scales with the text: a pass over 512 KiB gets four base budgets;
    - a request whose first token arrives after 2 s still runs its output rules;
    - **Reviewer A's S1 stream** (`^(\w+\s?){1,30}[.!?]` on the quarterly-report sentence, 4-character deltas, 100 ms budget) is decided with the whole-text hit list; with a comma before the terminator it is refused at the final pass, exactly where the whole-text scan is; an undecided rule over 4 KiB of held text refuses with `policy_regex_timeout`;
    - every decided `scan_or_hold` hit list equals a whole-text `finditer` on the incremental fuzz corpus.
  - **Limits:**
    - refusals for total expanded repetition, distinct case-insensitive items per pattern and per plan, **group depth, translated size and compile units per rule and per plan**, with one shared verdict: a refusal in the projector and the gateway, a non-blocking warning in control;
    - **v1 compatibility (D6):** a rule that v2 refuses still saves through control (`RuleWriteSerializer`), with the warning, and v1's `POLICY_SYNC` receives it unchanged; an org slugged `_unauthenticated` is not projected to v2, with a note, and nothing changes for v1;
    - **the nesting tests** (`nesting_test.py`): the depth scan equals `re`'s own recursion on every pattern `re` accepts; 499, 400, 100 and 17 deep are refused by every entry point; a comment or verbose comment full of `(` is not nesting; the 8 worst 16-deep shapes compile from a 600-frame stack; `RecursionError`, `MemoryError`, `SystemError` and `OverflowError` injected into `regex.compile` become refusals; a plan holding a 499-deep bomb checks without raising;
    - a hostile 64-rule plan, re-published with a new body every round, keeps the projector heartbeat inside `lag_ms`; **a plan holding a 499-deep row keeps the heartbeat fresh and refuses that rule;** pending plans are validated oldest-pending-first;
    - **issue #125's REWRITE case** (`^(([^#]+))+#` and `l(?:(\s?a)+)+$` with 24–28-character templates): the projector round and the gateway applier stay within their bounds (1 ms ticker), the template check is refused at its thread-CPU budget on the scan scheduler, and 3,000 good orgs are unaffected; **issue #126's case:** a round at 100k orgs with no plan changes does no per-org validation work;
    - event-loop lag while a worker compiles a plan at every per-plan limit stays within the switch interval plus the largest C compile call, against a pure-Python baseline in the same run (one rule per step, GIL yield between rules);
    - single-flight compile: 50 concurrent first requests trigger one compile;
    - the compiled-program cache stays within its byte budget, charged `80 × compile_units + 16 KiB`;
    - `_matches_own_pattern` cannot hang a control worker.
  - **CI jobs for the customer-regex evidence (D7).** These run on GitHub runners; this host's runs (`ev6/RERUN.md`) are evidence that the jobs pass today, not a substitute:
    - **per PR** that touches the translator, the scanner, the limits, the pin, the patch or the build (standard runners, a matrix so each leg stays near 10 minutes): the wheel rebuild and its sha256; `gen_cased.py --check`; the exhaustive class and two-sided boundary tests; one-sided boundaries; every case-insensitive literal; `alt_exhaustive.py`; `alt_negated.py`; `nesting_test.py`; `estimate_test.py`; the range classifier; `s1_test.py`; the differential fuzz on **all 12 seeds** (1–8, 303, 11–13) and the incremental fuzz on all 11; upstream `regex.test_regex`; the cross-tenant test at T = 2 and 4; the crash fuzz. On this host one pass of that list took 35 minutes (with 3 fuzz seeds; `RERUN.md`) of wall time on one core;
    - **weekly, and on PRs that touch the patch, the pin or the translator** (a larger runner, 16 cores): ASAN + UBSan on the patched wheel for 2 hours with 16 workers (32 worker-hours; this host ran 1 worker-hour: 287,725 patterns, 0 reports), the cross-tenant matrix at T = 2, 4, 8 and 16 (T needs at least T cores to mean anything), and the GIL convoy curve (`gil_duty.py`);
    - timing assertions use thread CPU; wall-time numbers are recorded, not asserted, because shared runners are noisy.
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
- **Regression:** the full gateway_v2 suite (`structural-gates`) and the control suites, by CI on GitHub runners (D7); a v1 check that `gateway/` and its compose service, image and route are unchanged by the PR's diff, and that the v1 golden suite (`golden-freeze`) fails identically to base.

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
| Engine swap changes what customer rules match | Translator from `re`'s own parse tree; a pinned `regex` whose Unicode database matches the interpreter's, checked at boot by a full functional comparison; exhaustive, fuzz and incremental-scan tests against `re` in CI, on the built wheel. Three `regex` bugs are worked around and pinned by tests (prefilter, lone negated characters in every spelling, unrolled repeat minimums); `re`'s own astral case-insensitive behaviour is reproduced |
| The `regex` pin is a **patched fork** of a frozen release (no cp312 wheel; upstream memory-safety fixes after it; process-CPU timeout fixed by our patch) | Hash-pinned sdist, patch and build backend; no build isolation, no network; a bit-reproducible wheel whose sha256 CI requires; boot check of the extension's sha256 and a functional marker; never emits the constructs the known bugs need; crash fuzz in CI and ASAN + UBSan weekly and on every change to the patch, pin or translator. **Fallback:** scans in worker processes (no fork needed; crash containment), if the fuzz ever finds a reachable memory-safety bug. **Exit plan:** explicit code-point sets built from `re`, exact on any `regex` release and 6–14× slower, with the clock patch re-applied |
| Inline streaming scans hold the loop | Incremental scanning (about 0.04 ms per delta for 10 rules, at any length, on punctuated prose and code); half a scan window of thread CPU per delta; an inline timeout moves the work off the loop instead of refusing; a per-org bucket (ρ = 25%) and a **worker-wide bucket (25%)**, refilled by the loop's available time; tenant B waits behind at most 18.3 ms of loop CPU |
| Off-loop scans of one org starve another | Scan scheduler: a FIFO per org, deficit round robin by thread CPU, at most T − 1 threads per org; depth bounded by admission; timeouts on the scanning thread's own CPU (patched clock), so another org's threads cannot shrink a budget. Wait ≤ (T + 2A)·b / T of scan CPU: a capacity statement whose levers are admission's per-org concurrency cap and T |
| One tenant's rule poisons the projector round, and so the fleet's stamp (B-2; live at HEAD) | PR #117 at HEAD. In F1a-1: an iterative depth scan (≤ 16) before any parser runs; every exception in checking, translating or compiling is a refused rule; a 499-deep row keeps the heartbeat fresh (test) |
| One tenant's rule is **slow** in the projector or the applier (issues #125, #126; live at HEAD for opt-in REWRITE) | The projector and the applier never run a customer regex; the REWRITE check runs on the scan scheduler with a real thread-CPU budget (patched clock); validation is memoised by body hash with per-plan caps (test) |
| Hostile plans slow the projector or the gateway | An iterative nesting check before any parser runs; a parse-only estimate of translated size and compile units (`regex` unrolls repeat minimums), capped per rule and per plan; a 50 ms translation CPU cap per rule in the gateway; the parse-only check memoised, time-boxed per round and validating oldest-pending-first; single-flight, per-rule, GIL-yielding lazy compile on the scheduler, whose one uninterruptible call the units cap bounds at about the switch interval; a byte-bounded program cache charged `80 × compile_units + 16 KiB` |
| Translated match cost exceeds `re`'s by more than the 6× gate for some shapes (case-insensitive keyword lists 4–8×, because of the prefilter guard; possessive nests 4–10×) | Bounded by the per-pass budget and charged to the org; gated at 10× for those shapes. **Follow-up (tracked):** narrow the guard to patterns that can start with an optional case-insensitive item, with the whole exactness suite |
| A non-monotone rule holds a stream while undecided | Retries at stoppers (once per growth step), on doubling and at the end; refused over 4 KiB of undecided text; only the final pass refuses |
| Thread CPU inflates under host contention (1.7× measured), so a pass near its budget can be refused | Ordinary passes must cost ≤ 50% of their budget (CI gate on the corpus); `amf_regex_pass_budget_fraction` and a log above half the budget |
| The GIL convoy: an off-loop pass's wall time grows with the loop's Python duty cycle (×1.3–1.7 at 25–50% duty, ×2.7–3.2 at 75–100%; ×3.9–12 for a fully busy loop, depending on the host) | No refusal or cross-tenant effect (budgets are thread CPU); the loop's customer-regex share is capped at 25%; wall/CPU histograms. **Follow-up (tracked, decided at F5):** a second patch that skips the GIL in the periodic check on non-main threads (prototyped and measured, §5.2.4), or the process fallback |
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

### Revision-5 findings (Plan Reviewer A 5857471619 and Plan Reviewer B 5857502319, at `e2c3e2cf`)

Disposition: **fixed** (the design changes; evidence shows it), **argued** (no design change; evidence shows why), or **deferred** (tracked, with the reason). `ev6/` is `docs/plans/evidence/2026-11-12-f1-plan-rev6/`.

| Finding | Severity | Disposition | Where |
|---|---|---|---|
| **A-B1** gateway translation cost unbounded through the range classifier (986 ms for 166 wide ranges; 6 rules held a worker 5.6 s) | BLOCKER | **Fixed.** Ranges classified by a bisect over a build-time `WORD_FLIPS` table (A's first option), exact against brute force; plus a 50 ms per-rule translation CPU cap in the gateway that refuses (A's second); A's pattern in the §5.8 tests | §5.2.1 (range classes, translation CPU cap); §5.2.3 limits; §5.8 Regex DoS |
| **A-S1** requirement 4 fails for non-monotone cost (`^(\w+\s?){1,30}[.!?]`) | SHOULD-FIX | **Fixed** with A's second option, hold and retry ("once"), with a 4 KiB undecided-hold cap; requirement 4 restated to the guarantee that holds | §5.2 requirement 4; §5.2.4 "Timeouts on non-final passes"; §5.8 No false refusals |
| **A-N1** the raw merge bug is wider (non-capturing groups, astral) | NIT | **Fixed.** Text widened; building the test found a third spelling the workaround missed, one-code-point ranges (`[^a-a]`, an under-match), now fixed: every one-code-point negated set is doubled, through one function | §5.2.1; §5.8 (`alt_negated.py`) |
| **A-N2** §5.8 should name the S1 case and the B1 pattern | NIT | **Fixed** | §5.8 No false refusals; Regex DoS |
| **B-1** `regex`'s timeout counts process CPU, so one tenant's scans shrink every other's budget | BLOCKER | **Fixed** with B's first option: a thread-CPU patch to `_regex.c`, built reproducibly from the hash-pinned sdist; B's 60%-of-budget test added (0 refusals with 1, 3 and 7 burning org-A threads). Processes kept as the documented fallback. Two consequences B's finding exposed are designed for: the GIL convoy (wall time) and thread-CPU inflation (headroom) | §5.2 requirements 2 and 6; §5.2.2 (patch, build, boot check); §5.2.4 (unit, headroom, convoy); §5.8 (patched wheel) |
| **B-2** a nesting-depth bomb (499 levels) raises `RecursionError` everywhere and, at HEAD, poisons the projector round | BLOCKER | **Fixed.** Depth ≤ 16 by an iterative scan before any parser; every exception is a refused rule in control, the projector and the gateway; B's test added. The HEAD part ships as PR #117 ahead of F1a-1 | §5.2 requirement 7; §5.2.1 Parsing; §5.2.3 (limit, why 16, projector); §5.8 Limits |
| **B-3** inline share capped per org but not in aggregate; DRR bound unstated | SHOULD-FIX | **Fixed.** A worker-wide inline bucket (25%, burst 10 ms) refilled by loop-available time, with its bound (18.3 ms of loop CPU); the DRR bound as a formula of active orgs × per-pass budget, simulated | §5.2.5; §5.8 test (a2), (d) |
| **B-4** translated size and compile cost understated; the limits miss them | SHOULD-FIX | **Fixed.** Translated-size and compile-units caps from a parse-only estimate (real ≤ estimate on the fuzz corpus); corrected cost table; the stall bound restated against a pure-Python baseline; the cache charged by compile units. Found while fixing it: `regex` unrolls repeat minimums (a GIL-holding compile bomb that passed every revision-5 limit) | §5.2.1; §5.2.3 |
| **B-5** supply chain: hash-pin the sdist, the builder image and the backend; record the fork; advisories | SHOULD-FIX | **Fixed** for the sdist, patch and backend (hash-pinned, no build isolation, reproducible wheel, CI requires its sha256), the fork record and the boot check. **Deferred** for the builder image digest: Docker Hub rate-limited the manifest query and this host has no Docker; it is recorded in the PR that adds the builder stage | §5.2.2; §9 |
| **B-6** §5.2.4's "counts the matching thread's CPU time" | NIT | **Fixed** | §5.2.2; §5.2.4 |
| **B-7** the projector time box should validate oldest-pending-first | NIT | **Fixed** | §5.2.3 projector; §5.8 Limits |
| (found in revision 6) `regex` unrolls repeat minimums: `(a)+` nested 16 deep compiled in 166–310 ms in one GIL-holding call; 32 deep exhausted memory | — | **Fixed** by the compile-units cap | §5.2.1; §5.2.3 |
| (found) `(?:[^a-a]\|[^b-b])` under-matched (a bypass) | — | **Fixed** | §5.2.1 |
| (found) variable-width lookbehinds were accepted and translated | — | **Fixed**: refused, as `re` does | §5.2.1 |
| (found) the prefilter guard makes case-insensitive keyword lists 4–8× slower than `re` | — | **Argued** (bounded by the per-pass budget, charged to the org, gated at 10×) and **deferred** (narrow the guard; needs the whole exactness suite) | §5.2.1; §5.8 gate; §9 |
| (found) CPython 3.12's `re` drops an astral literal from a multi-member case-insensitive set | — | **Argued**: requirement 1 is `re`'s meaning, so it is reproduced; the estimate pads for it | §5.2.1 |
| (found) stock timeouts: NaN means no limit, ≥ 9.2e12 s fires at once; any `CFLAGS` drops `-O2 -DNDEBUG`; pip build isolation ignores `--require-hashes` for the backend; the boot check cannot tell a patched from a stock wheel | — | **Fixed** | §5.2.2; §5.2.4 Calls |
| (input, 12 Nov) issue #125: a slow REWRITE template check stalls the projector round and every gateway applier while `re` holds the GIL | High | **Fixed in the design:** the projector and applier never run a customer regex; the check runs on the scan scheduler with the patched thread-CPU budget; #125's harness case is a §5.8 test | §5.1; §5.2.3; §5.8; §9 |
| (input, 12 Nov) issue #126: validation cost per round unbounded; cache by body hash; per-plan caps | — | **Fixed in the design** (memoised by body hash; per-plan caps; gateway pattern length) | §5.2.3; §5.8 |
| (input, 12 Nov) PR #117's reviewers recommend `MAX_GROUP_DEPTH` 16 | — | Agreed: 16 here and recommended for #117 | §5.2.3 |

### Rev 6 change log

| Finding | Plan section | Evidence file (under `ev6/`) |
|---|---|---|
| A-B1 | §5.2.1, §5.2.3, §5.8 | `range_b1.py`, `r6_range_b1.txt`; `translate_prototype.py` (`_range_class`, `TRANSLATE_CPU_BUDGET_S`); `gen_cased.py` (`WORD_FLIPS`); `rev5_e2c3e2cf/` (the before) |
| A-S1 | §5.2, §5.2.4, §5.8 | `LIMITS6.md` §4; `incremental.py` (`scan_or_hold`); `s1_test.py`, `s1_hold.py`; `r6_s1_test.txt`, `r6_s1_hold.txt` |
| A-N1 | §5.2.1, §5.8 | `LIMITS6.md` §5; `alt_negated.py`; `r6_alt_negated.txt` |
| A-N2 | §5.8 | — |
| B-1 | §5.2, §5.2.2, §5.2.4, §5.8 | `clock/CLOCK.md`; `clock/regex-2023.8.8-thread-cputime.patch`; `clock/build_patched_regex.sh`; `clock/results/` (11 Nov host) and `clock/results/host2_*` (12 Nov host); `clock/run-2/` (exactness on the patched wheel); `clock/results/asan-fuzz-host2/` |
| B-1: GIL convoy | §5.2.4, §9 | `clock/CLOCK.md` §4.4; `clock/probes/gil_probe.py`, `gil_duty.py`; `clock/results/gil_*.txt`, `host2_gil_*.txt` |
| B-1: thread-CPU inflation | §5.2, §5.2.4, §5.8, §9 | `clock/CLOCK.md` §4.3, §4.7; `clock/results/cpu_variance.txt`, `host2_cpu_variance.txt`, `cross_tenant_*_k4.txt` |
| B-2 | §5.2, §5.2.1, §5.2.3, §5.8, §9 | `LIMITS6.md` §1; `nesting_test.py`; `r6_nesting.txt`; PR #117 |
| B-3 | §5.2.5, §5.8, §9 | `LIMITS6.md` §3; `inline_bucket6.py`, `drr_bound.py`; `r6_inline_locked.txt`, `r6_drr.txt` |
| B-4 | §5.2.1, §5.2.3, §9 | `LIMITS6.md` §2; `perf6.py`, `perf6.md`, `r6_perf6.log`; `estimate_test.py`, `r6_estimate.txt`; `stall6.py`, `r6_stall.txt` |
| B-5 | §5.2.2, §9 | `clock/CLOCK.md` §3; `clock/build_patched_regex.sh`, `clock/build-requirements.txt`; `RERUN.md` (wheel hashes on the 12 Nov host) |
| B-6 | §5.2.2, §5.2.4 | `clock/CLOCK.md` §1 |
| B-7 | §5.2.3, §5.8 | `LIMITS6.md` §7 |
| Issues #125, #126; #117 depth | §5.1, §5.2.3, §5.8, §9 | issues #125, #126; PR #117 reviews; `clock/CLOCK.md` §4.3 (the budget fires at the thread's own CPU) |
| Found in revision 6 | §5.2.1–§5.2.4, §9 | `LIMITS6.md` §2.1, §2.2, §2.4, §5; `clock/CLOCK.md` §1, §3, §8; `clock/results/edge_*.txt`, `host2_edge_*.txt` |
| Regression of everything above | §5.2.1 evidence table | `RERUN.md`; `r6_*.txt` (unpatched wheel); `clock/run-2/r_*.txt` (patched wheel) |

---
_Generated by [Claude Code](https://claude.ai/code/session_01SDCVQVj8LRKW3CZ99zfdW1)_
