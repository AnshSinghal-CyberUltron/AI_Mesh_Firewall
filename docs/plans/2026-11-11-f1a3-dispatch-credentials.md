# F1a-3: OpenAI-compatible dispatch and model credentials (design)

Status: 11 Nov 2026, **design revision 1**, for review before any code.
- Parent: `docs/plans/2026-11-11-f1-production-serving.md` (PR #103, branch `feat/f1a-production-serving`, revision 5). §7 of the parent is the binding requirement list for this slice. §1–§4, §6 and §8 are its context.
- Sibling: F1a-2 (MODEL state) is designed in parallel on `plan/f1a2-model-state`. This plan depends only on the interface parent §6 states. §9 lists the exact interface needed.
- Base: `develop` at `25c33c86`. Every file:line below was re-checked there.
- Evidence: `docs/plans/evidence/2026-11-11-f1a3-dispatch/` (prototypes, raw numbers, `RESULTS.md`).

Owner decisions this slice serves (parent §2): **D1** all v1 providers; F1a-3 ships the OpenAI-compatible family. **D2** envelope-encrypted keys in signed state, no control-plane call on the request path. **D3** live propagation within seconds. **D4** env-var keys refused in production. **D5** local first, hardware-independent; a KEK provider interface with a local KEK now and cloud KMS later.

## 0. Summary

1. **Credentials.** A three-level envelope: a fleet **KEK** (from a KEK provider: local `_FILE` now, cloud KMS later) wraps a random per-org **DEK**. The DEK seals each model credential with AES-256-GCM, a fresh 96-bit random nonce, and an AAD that binds the credential to its namespace, org, row, alias, provider, base URL, upstream model, DEK and credential version.
   - The sealed DEK and the sealed credentials travel inside the org's signed MODEL record (F1a-2's kind). A gateway opens a credential per request (0.55 µs measured with a cached cipher, 2.1–2.4 µs cold) and never caches plaintext.
   - No control-plane call happens on the request path, and no KMS call either: KMS, when it comes, is called once per process per KEK.
2. **Dispatch.**
   - One pooled HTTP client per origin `(scheme, host, port)` with `trust_env=False`, no redirects, no default auth header and resolve-then-pin dialling.
   - Per-provider request shaping (a `drop_params` equivalent) from a closed profile table.
   - An upstream SSE decoder that accepts the framings providers actually send.
   - A codes table where every code has a status, a type, a failure class and a console stage.
   - Bounded-cardinality series and the compose `gateway-v2-prod` service.
3. **Scaling.** Measured, not guessed. Credential work is under 1% of dispatch CPU. Two things decide the cost per worker:
   - **The HTTP client.** httpx, today's client, costs **≈3.2 ms** of CPU per streamed request at 32 in flight, rising to ≈5 ms at 128. aiohttp costs **≈0.36 ms** with the same decoder and stays flat. At 100k RPS that is ≈316 cores against ≈36 for dispatch alone (§6).
   - **Pool sizing.** develop's sizing gives **one** upstream connection per worker on this host (§1, C4).

   The design fixes both.
4. **Defects at develop HEAD that this design closes** (§1, each with a probe):
   - The shipped client honours `HTTPS_PROXY` (C3).
   - A refused destination is rendered `routing_internal_error` (C6).
   - One upstream connection per worker serialises streams (C4).
   - `100.64.0.0/10` passes the SSRF check (C5).
   - A 0.8 s / 1.0 s mid-stream bound would cut real model streams (C13). No §7 item covers it (§10, A4).

## 1. Current behaviour (develop `25c33c86`)

| # | Finding | Evidence |
|---|---|---|
| C1 | Production serves no completions. `provider` is an empty `StubProviderClient`, and `apply_production` never replaces it. `build_routed_provider` is imported into the production profile only as a re-export | `edge/serve.py:175-176,233,247`; `edge/production_profile.py:55-56,518-540` |
| C2 | `OpenAIUpstreamProvider` is one client per instance. Its single `api_key` becomes a **default** `Authorization` header, and its `base_url` is fixed at construction. So per-org keys and per-org origins cannot be expressed | `dispatch/openai_upstream.py:96-110,146-159` |
| C3 | The shipped client factory leaves `trust_env` at its default (`True`). With `HTTPS_PROXY` in the environment, every `https://` provider request, bearer included, is mounted on a proxy transport. Measured: `trust_env True`, the `https://` mount's pool is `AsyncHTTPProxy`, and the default `authorization` header is set (`probe_trust_env.txt`). There is no SSRF check on the chat path: `PinnedTransport` is used only by GW32 OAuth | `dispatch/openai_upstream.py:103-108`; `dispatch/pinned_transport.py:1-8` |
| C4 | **One upstream connection per worker.**<ul><li>The dev profile sizes the provider pool with `pool_size(PROVIDER)`, which is `workers()` (1 on this 2-CPU host, measured). Its pool wait is `max_stream_duration_s()` (300 s).</li><li>A second concurrent stream on a worker therefore queues behind the first for up to 300 s. Measured: 4 concurrent streams, each held 1 s by the upstream, complete at **1.35 / 2.36 / 3.37 / 4.38 s**, one after another (`probe_pool.txt`).</li><li>`build_routed_provider` divides that same number across clients (`provider_pool_share`).</li></ul> | `runtime/resources.py:349-356`; `edge/dev_profile.py:302-311`; `edge/routing_boot.py:123-133`; `runtime/resources_routing.py:63-70`; `probe_dispatch.txt`, `probe_pool.txt` |
| C5 | **SSRF check gaps.**<ul><li>`PinnedTransport` re-resolves on **every** request (`getaddrinfo` on the loop's executor).</li><li>`resolve_checked` does not apply `is_global`, so `100.64.0.1` (RFC 6598) is **allowed** today.</li><li>NAT64, 6to4, Teredo and `::/96` addresses are refused only by accident of Python's tables (`::/8` reserved, `2002::/16` private). An allowlisted host takes the `block_private=False` branch, and a 6to4 address embedding `169.254.169.254` would then pass.</li></ul> | `dispatch/pinned_transport.py:39-52`; `dispatch/url_guard.py:203-228,269,278-288`; probe `ssrf_probe_py312.json` |
| C6 | `PinnedRefused` subclasses `Exception`, not `httpx.HTTPError` or `OSError`, so the provider's `except` clauses miss it. It escapes into the routed provider's catch-all and is rendered `routing_internal_error`. Verified by a probe (`probe_dispatch.txt`) | `dispatch/pinned_transport.py:26`; `dispatch/openai_upstream.py:211-214`; `dispatch/routed_provider.py:295-298` |
| C7 | A routing refusal loses its candidate: `_refusal` builds the decision with `cand=None` | `dispatch/routed_provider.py:302-313` |
| C8 | **Upstream SSE decoder gaps.**<ul><li>Events are split on `\n\n` only. A CRLF-framed stream yields **0 frames** and never finishes, which ends as `stream_malformed_upstream`.</li><li>A comment-only keep-alive (`: ping`) sets the malformed latch. Both are measured in `ssrf_probe_py312.json`.</li><li>`finish_reason` must be one of four values.</li><li>A `usage` object is ignored.</li><li>`tool_calls` and `finish_reason` are decoded today, so S1 F6 is fixed.</li></ul> | `edge/wire/sse_decode.py:18,132-148,189-211,251-306` |
| C9 | `/v1/completions` `prompt` **is** folded and scanned as `completion_prompt` (S1 F5 fixed). The route reuses the chat handler, and the response is chat-shaped: no `text_completion` object | `edge/wire/openai_fold.py:78,147-149`; `edge/routes.py:499-500` |
| C10 | The upstream is always asked to stream (`stream: true`). `stream_options` is accepted from clients but never forwarded, so no upstream `usage` reaches charging (the v2-enforcement-leaks finding 4 gap) | `edge/upstream_body.py:149`; `edge/wire/openai_params.py:63-79` |
| C11 | The same forwarded set goes to every provider: `seed`, `logit_bias`, `reasoning_effort` and the `developer` role. There is no per-provider shaping and no reasoning-model budget rename | `edge/wire/openai_params.py:61`; `edge/upstream_body.py:167-176` |
| C12 | Embeddings ride the chat stream seam. With the OpenAI provider they end as `upstream_request_unbuildable` (500), after `mark_dispatched` | `dispatch/openai_upstream.py:192-196`; S1 plan §4a |
| C13 | **Stream timeout bounds** (contract on this host, measured):<ul><li>first event 30 s;</li><li>mid-stream **gap 0.8 s**;</li><li>total 299.94 s.</li></ul>After the first event, the edge cuts a stream with no upstream event for `inter_chunk_timeout_s()` = **1.0 s**. Real models pause longer than 1 s mid-stream (reasoning, tool arguments, coarse vendor chunking) | `runtime/resources_routing.py:37-54`; `edge/stream_timeouts.py:175-191`; `probe_dispatch.txt` |
| C14 | **Observability gaps.**<ul><li>The `routing_decision` log line has no request id, although `decision.request_key` **is** the request id.</li><li>`pipeline_trace` allows only `final_action`, `plan_version` and `stages`.</li><li>There are no upstream series.</li><li>`upstream_auth_error` gets no `x-should-retry` header: `retry_headers` is used only for incident codes.</li></ul> | `edge/routing_trace.py:205-217`; `edge/chat_upstream.py:254-260`; `audit/console_schema.py:51`; `edge/errors.py:312,421-445` |
| C15 | **Credentials today.**<ul><li>Control Fernet-encrypts model keys with `ZEROSHIELD_MODEL_KEY_ENCRYPTION_KEY`, or with a key derived from `SECRET_KEY`.</li><li>The shared builder **hashes a malformed configured key into a valid one** and falls back to a literal seed.</li><li>Recoverable `GatewayAPIKey` secrets use the **same** cipher.</li><li>An undecryptable key becomes `""` plus a warning.</li><li>v1 pushes the Fernet tokens into unsigned Redis (`llm:model_configs:{org}`). The v1 gateway decrypts them at reload, again falling back to `DJANGO_SECRET_KEY`, and keeps **plaintext for every model** inside LiteLLM.</li><li>v1 retries twice (`num_retries=2`), drops unsupported params, times out at 120 s, and applies **no SSRF check** to `api_base`. Control does not validate `api_base` either.</li></ul> | `control/.../core/models.py:486-498,1565-1568,1709-1743`; `shared/ai_mesh_shared/llm_model_crypto.py:26-36`; `gateway/ai_mesh_gateway/config_sync.py:26-27`; `gateway/ai_mesh_gateway/llm_router.py:685-687,911-929,1025-1026`; `control/.../core/llm_model_serializer.py:63-89` |
| C16 | Signed state is HMAC-SHA256 under `AMF_STATE_HMAC_KEY`, read from the environment and held by the projector, the re-hydrators **and every gateway**. No component reads `_FILE` secrets. The kinds are PLAN, KEY, KS and BUDGET | `edge/state_runtime.py:128-151`; `runtime/state_sig.py:65-91`; `domain/state.py:43-49` |
| C17 | **Compose.**<ul><li>There is no production gateway service. The dev `gateway-v2` takes its provider key from an env var.</li><li>The shared `redis` publishes `6379` on all interfaces with no auth, and the state services sign into it.</li><li>`AMF_FD_LIMIT` exists in the contract and is never set.</li></ul> | `docker-compose.yml:63-72,491-541,577`; `runtime/cgroup.py:242-248` |
| C18 | `cryptography` is not a gateway_v2 dependency (control already uses it) | `gateway_v2/pyproject.toml:10-13` |

What is already right and is kept:
- the per-request iterator close (`abort()` is a no-op), which fixes S1 F3/F4 (`openai_upstream.py:23-27,174-176`; `routed_provider.py:205-207`);
- the deferred SSE head, so a pre-content refusal is a real HTTP error (`edge/stream_response.py:1-10`);
- the GW28 single-open-site routing loop with pre-content fallback (`routed_provider.py:243-300`);
- verified-delta rendering (`edge/upstream_body.py:54-111`).

## 2. Credentials (§7.1, §7.2, D2, D4, D5)

### 2.1 Key hierarchy

```
KEK provider (local _FILE now; cloud KMS later)          held by: projector, gateways
  └─ KEK  kid=k2026q4   AES-256, fleet-wide, rotated on a fixed cadence
       └─ per-org DEK   dek_id=d20261111-9f3a5c1e  AES-256, random, one live per org
            ├─ K_cred = HKDF-SHA256(DEK, info="amf.cred.key.v1")
            │    └─ credential  {v, n, ct, s}  AES-256-GCM(K_cred, n, key, AAD_cred)
            └─ K_memo = HKDF-SHA256(DEK, info="amf.memo.v1")  (projector memo digest only)
```

Why three levels rather than the single `kid` level of §7.1:
- **KEK rotation re-wraps one DEK per org.** It does not re-encrypt every credential: 100k wraps instead of 500k encryptions, with no AAD change to the credentials.
- **A cloud KMS fits without a request-path or per-org call.** The KMS unwraps the KEK once per process. The DEKs and the credentials are opened locally in microseconds.
- **Cryptographic tenant separation comes in addition to AAD.** A canonicalisation bug in AAD alone cannot make org B's key open org A's ciphertext.
- **Later options are possible:** a customer-managed key per org (wrap that org's DEK under the customer's KMS key), or KEK-per-cell sharding to bound the blast radius of a host compromise (§2.11, owner question Q8).
- **The nonce budget is trivial.** A DEK seals at most a few rows per edit. A KEK wraps one DEK per org per rotation, which is well below the 2^32 random-nonce limit per key (NIST SP 800-38D §8.3).

§7.1's rules map onto the two levels in §2.3. Nothing in §7.1 is dropped; the `kid` term is refined (§10, amendment A1).

### 2.2 Algorithms

- **AEAD.** AES-256-GCM (`cryptography` `AESGCM`), with a 128-bit tag and a 96-bit nonce from `os.urandom(12)` for every encryption, at both levels. AES-GCM-SIV or XChaCha20-Poly1305 would also work, but §7.1 binds GCM, and random 96-bit nonces are safe at these invocation counts.
- **Dependency.** `cryptography` becomes a gateway_v2 base dependency. Control and v1 declare only `>=43.0.0` (`control/pyproject.toml:11`, `gateway/pyproject.toml:8`); gateway_v2 pins an exact version with Python 3.12 wheels (the benchmarks used 46.0.3) and adds it to the image's hashed install.
- **Library check at boot.** One AES-GCM known-answer test (a NIST vector) runs at boot. A failure refuses to start.

### 2.3 AAD and the two envelopes

AAD encoding:
- `enc(domain, f1, …, fn)` = `u32be(len(domain)) ‖ domain ‖ Σ u32be(len(fi)) ‖ utf8(fi)`.
- Every field is a `str` and is taken **byte-for-byte from the signed record body**. The gateway never reformats a field.
- Length-prefixing makes the encoding injective, so `("a|b","c")` ≠ `("a","b|c")`.

| Level | AAD | Rules from §7.1 that apply here |
|---|---|---|
| DEK wrap (KEK) | `enc("amf.dek.v1", namespace, org, dek_id, kid)` | <ul><li>Fresh nonce.</li><li>Explicit encrypt `kid` (`AMF_CRED_ENCRYPT_KID`, no default and no "newest" heuristic).</li><li>Switching it is gated on `amf_gateway_cred_kids{kid}` (§2.8).</li><li>A kid is retired only when nothing uses it and the fleet cursor has passed the re-wrap.</li><li>Fixed cadence, so destroying a kid makes old store dumps and backups unreadable.</li></ul> |
| Credential (DEK) | `enc("amf.cred.v1", namespace, org, row_id, alias, provider, base_url, upstream_model, dek_id, str(v))` | <ul><li>Fresh nonce.</li><li>The whole sealed tuple `{v, n, ct, s}` is reused **only** when the plaintext and the AAD are byte-identical (§2.5).</li></ul> |

Why each credential field is in the AAD:
- **`org`, `row_id`, `alias`:** a ciphertext cannot be moved to another tenant or another row.
- **`provider`, `base_url`:** a holder of the HMAC key but not of a KEK cannot redirect a credential to a host of its choosing. The re-hydrators hold the HMAC key and no KEK (§2.11).
- **`upstream_model`:** a sealed key cannot be re-pointed at a different upstream model without the DEK.
- **`dek_id`:** binds the credential, transitively, to the KEK kid named in the DEK's own AAD.
- **`v`:** the projector's per-row credential version. It rises whenever the plaintext or any other AAD field changes, so an old ciphertext cannot be placed under a newer version.
- **Limits:** the AAD cannot stop a whole, self-consistent old record from being replayed. Signed versions and manifest anti-regress do that (`runtime/state_sig.py:6-13`; F1a-2 §6.2). §2.11 states the cold-fetch residual.

### 2.4 What the signed MODEL record carries

The F1a-2 record body for one org, with the credential fields owned by this slice. All binary values are base64url without padding.

```json
{
  "dek": {"kid": "k2026q4", "id": "d20261111-9f3a5c1e", "n": "<12 B>", "w": "<48 B>"},
  "models": [
    {"row": "4127", "alias": "prod-chat", "provider": "openai",
     "base_url": "https://api.openai.com/v1", "model": "gpt-4o-2024-08-06",
     "cred": {"v": 3, "n": "<12 B>", "ct": "<len(key)+16 B>", "s": "<16 B>"}},
    {"row": "4130", "alias": "local-llama", "provider": "ollama",
     "base_url": "http://10.20.0.7:11434/v1", "model": "llama3.1:8b", "cred": null},
    {"alias": "legacy", "omitted": "credential_env_var"}
  ],
  "egress": [{"scheme": "http", "host": "10.20.0.7", "port": 11434}]
}
```

- `s` is the projector's memo digest: `HMAC-SHA256(K_memo, fernet_token)[:16]`.
  - `K_memo` is derived from the org's own DEK under its own HKDF label, separate from `K_cred` (key separation), so it costs no new secret.
  - A gateway that holds the DEK learns nothing from it, because a Fernet token is random ciphertext.
  - It lets a restarted projector skip unchanged rows for **1.9 µs** each instead of **12–15 µs** for a Fernet decrypt. At 500k rows that is about 1 s instead of about 7.8 s (`aead_results.json`: `hmac_*`, `fernet_*`).
- `dek` is absent when the org has no sealed credential.
- `egress` lists the org's private-target exceptions (§4.3).
- Nothing else about a credential is in state: no last-4, no length, no env-var name.

### 2.5 What control stores, and the projector's sealing round

Control (system of record):
- **`LLMModelConfig.encrypted_api_key` stays the Fernet token**, so storage is unchanged. In production:
  - the model key is read from `ZEROSHIELD_MODEL_KEY_ENCRYPTION_KEY_FILE` as a `MultiFernet` list, for rotation;
  - the `SECRET_KEY` fallback is refused, as is the literal seed;
  - a malformed key **refuses to start** instead of being hashed (`llm_model_crypto.py:28-32` → raise when `AMF_PROFILE=production`). This is §7.1's last bullet.
- **`GatewayAPIKey.encrypted_secret` moves to its own key**, `ZEROSHIELD_GATEWAY_KEY_ENCRYPTION_KEY_FILE` (§7.1). The data migration reads with both keys, writes with the new one, and keeps both for one release.
- **New table `OrgPrivateEgress(org, scheme, host, port, note, created_by, created_at)`.** Only platform staff may edit it (Django admin, `is_superuser`), never an org admin (§4.3).
- **D4.** `api_key_env_var` stays for v1. The console marks such rows "not served by the v2 production gateway".
- **Control never sees a KEK or a DEK.**

Who holds which secret:

| Secret | control | projector | re-hydrator | gateway |
|---|---|---|---|---|
| model key (Fernet, `_FILE`) | yes | yes (decrypt only) | no | no |
| `GatewayAPIKey` key (`_FILE`) | yes | no | no | no |
| KEK(s) (`AMF_CRED_KEKS_FILE`) | no | yes | no | yes |
| state HMAC (`AMF_STATE_HMAC_KEY_FILE`) | no | yes | yes | yes (Ed25519 follow-up: public key only, §7.8) |
| Valkey user | — | `projector` (state write) | `rehydrator` (state write) | `gateway` (state read-only, §3.11) |

The sealing round runs inside F1a-2's projector round, using this slice's `state_control/cred_seal.py`. For each active row:
1. **Omission rules** (reason codes in §3.8):
   - `api_key_env_var` set → `omitted: credential_env_var` (D4, layer 1);
   - inactive → absent;
   - keyless → `omitted: credential_missing`, except the private-egress case in §10 A2;
   - a key that fails the charset check (printable ASCII `0x21–0x7E`, no whitespace, 8–4096 bytes) → `omitted: credential_invalid_format` (§7.2);
   - a base URL that fails §4 → `omitted: base_url_refused`;
   - a provider outside this build's shipped set → `omitted: provider_unsupported` (Azure, Anthropic and Bedrock until F1b and F1c).
2. **DEK.** Reuse the org's live DEK, unwrapped from its last published record; on the first credential, generate `os.urandom(32)` with a fresh `dek_id`. Rotate it when its age passes the DEK cadence (Q1), as a bounded backlog after the heartbeat.
3. **Reuse rule (§7.1).** Reuse the previous `{v, n, ct, s}` only if `s` matches the current Fernet token **and** every AAD field is byte-identical. Otherwise Fernet-decrypt (≈14 µs), check the charset, set `v ← v+1`, encrypt with a fresh nonce (1.0 µs) and recompute `s`. The plaintext is a local variable of one function and is never logged (§2.7).
4. **Order (parent §6.6).**
   - Removals and real per-row key changes are written **before** the heartbeat.
   - KEK re-wraps and DEK rotations form a bounded backlog after it, at a contract-derived count per round.
   - A full re-encrypt of 100k orgs × 5 models costs ≈0.5 s of AES (`full_reencrypt_500k_s`) plus ≈7 s of Fernet decrypts. That is why it is a backlog and never a pre-heartbeat step.
5. **Hardening.** The projector reads every secret from `_FILE` (§7.1). It runs with `RLIMIT_CORE=0` and `PR_SET_DUMPABLE=0` (same code as parent §5.6), as a non-root user with a read-only root filesystem. The plaintext rule of §2.7 covers its logs.

### 2.6 Gateway: where plaintext lives

Apply path (F1a-2's applier calls `CredentialOpener.check_org(record)`):
- unwrap the DEK under its kid;
- decrypt each sealed credential once and discard it immediately;
- precompute each entry's AAD bytes. Encoding costs **1.65 µs**, more than the decrypt itself, so it is done once per entry and not per request.

For a full cold apply of 100k orgs × 5 models this is ≈100k × 2.1 µs + 500k × (0.6 + 1.7) µs ≈ **1.4 s per worker**, at boot or on a full resync. Deltas cost per changed org.

On failure:
- unknown kid or a DEK tag failure → the org-level `unavailable(version)` marker (parent §6.2);
- one entry's tag failure → only that entry becomes `omitted: credential_unreadable`;
- both are counted in `amf_cred_open_failures_total{reason}`.

The view holds ciphertext only: the wrapped DEK, plus `{n, ct, aad}` per entry, decoded from base64 once at apply time.

Request path, `open(view, entry) -> bytes`:
1. Get `AESGCM(K_cred)` from a **bounded LRU keyed `(org, dek_id)`**. A miss adds one HKDF (≈2 µs, the same order as the measured HMAC) to the cold path.
   - Each cipher object is 2.4 KB of RSS (measured), so the cache is bounded rather than holding one for every org.
   - The capacity is contract-derived: `cred_cipher_cache_entries()`, ≈4096 entries or ≈10 MB per worker at the defaults.
   - The alternative, holding raw DEK bytes for every org, costs only 90 B per org (9 MB at 100k orgs), but rebuilds the cipher on every request (1.6–2.0 µs). The bounded cipher LRU is the better trade.
2. Decrypt into the `Authorization` header bytes.

The plaintext key exists:
- in one `bytes` value for the duration of `open`;
- in the request's header list until the HTTP client has written the request;
- **never** in a cache, a log, an exception or a trace.

The ciphertext is opened again on the next request. Python cannot zero memory, so the defence is a short lifetime plus process hardening:
- `RLIMIT_CORE=0` and `PR_SET_DUMPABLE=0` in the parent and in every worker (parent §5.6);
- no swap: compose sets `memswap_limit` equal to `mem_limit`;
- a non-root user, a read-only root filesystem, `cap_drop: ALL`, `no-new-privileges`.

Why plaintext is never cached:
- A per-org plaintext cache would save ≈0.5 µs per request (0.05 cores at 100k RPS).
- In exchange it would keep every served org's provider key resident, exposed to any heap disclosure.
- It would also need its own invalidation on revocation.
- The cipher cache holds key schedules, which add nothing beyond what the resident KEK already implies (§2.11).

### 2.7 Leak controls (§7.2)

- **No dataclass protocol.**
  - `SealedCredential` and `SealedDek` are plain slotted classes with redacted `__repr__`. They have no `__eq__` over the secret fields. `__reduce__`, `__getstate__` and `__copy__` raise `TypeError`, so pickling and copying fail. Deleting the dataclass hooks, as `vector_credentials.py:98-101` does, is not enough on 3.11+, because `object.__getstate__` pickles slots. Measured: `OrgCredential` still pickles with its secret in the bytes (`probe_pickle.txt`). That is an existing GW30 leak path, reported here and not fixed by this slice.
  - An AST gate (`lint/check_credential_types.py`) bans `asdict`, `astuple`, `vars`, `__dict__`, `copy.copy`, `pickle` and `json.dumps` on those types and on the opener's return value.
  - `lint/check_credential_flow.py`'s scope grows to every module whose name contains `cred`, plus `dispatch/upstream_*.py`.
- **Errors carry names only.** Dispatch logs and traces carry exception **type names** only, never `str(exc)`, the URL with its query, headers or a response body. Error bodies from providers are read, bounded (§3.7), only to classify them, and are never echoed or logged.
- **Planted-key grep (test plan §8, T-L1).** A unique `sk-plant-<nonce>` key is sealed for a test org. The run then greps for it in:
  - gateway, projector and re-hydrator logs;
  - `/metrics`;
  - SSE and HTTP bodies;
  - `amf.trace` frames;
  - audit stream rows and control console rows;
  - boot logs;
  - error envelopes for every code in §3.8.

  It must appear **only** in the recorder's received `Authorization` header.

### 2.8 Rotation without downtime

| What | Steps | Why no downtime |
|---|---|---|
| **KEK** (cadence: Q1, proposed 90 days) | <ol><li>Add `k_new` to every gateway's KEK file and roll the fleet. Each process exports `amf_gateway_cred_kids{kid="k_new"} 1`.</li><li>**Gate:** the projector accepts `AMF_CRED_ENCRYPT_KID=k_new` only once every live gateway's kid heartbeat (`{ns}:fleet:kids:<gateway_id>`, TTL 3 × refresh, the one write family the `gateway` ACL allows for this) lists `k_new`. Otherwise it keeps the old kid and raises `CredKidNotFleetWide`.</li><li>Re-wrap every org's DEK under `k_new` as a bounded backlog. The credentials are untouched.</li><li>Retire `k_old` when no published record names it **and** every live gateway's heartbeat reports an applied MODEL `feed_seq` at or past the last re-wrap. Remove it from the gateways, then from the projector, then destroy it. The heartbeat value is `{kids, model_feed_seq}`; there is no cursor series today, only `amf_state_lag{kind}` (`runtime/state_metrics.py:10-22`).</li></ol> | Gateways hold both kids while records move. A gateway started from an old deployment config without `k_new` refuses to start, because `AMF_CRED_REQUIRED_KIDS` is checked at boot. It never serves with orgs it cannot open. |
| **DEK** (cadence: Q1, proposed 30 days, staggered by `hash(org)`) | New DEK; re-encrypt that org's ≤N credentials; one new record version | The DEK travels with the data. A record carries one DEK and all of its credentials, so the switch is atomic per org. |
| **Control model key** | Prepend the new key to the `MultiFernet` list and run the rotation task. Every token changes, `s` mismatches everywhere, and the projector re-seals as a backlog | Gateways never see the Fernet layer. |
| **Customer rotates their provider key** | Console edit → new Fernet token → `v+1` → published | Bounded by §2.9. |

### 2.9 Revocation latency

A console edit that removes, disables or replaces a key reaches every gateway within **projector `lag_ms` + `fresh_ms`, 21 s with the defaults**, the bound in parent §6.10. Normally it takes one projector round plus a nudge, about 1–2 s. L36-1 step 7 (§8) measures it.
- Removals run before the projector heartbeat (§2.5).
- A gateway that cannot verify fresh state fails closed (`runtime/state_stamp.py:18-29`).
- Nothing on the gateway caches plaintext, so applying the record is the revocation.
- **In-flight streams opened with the old key run to completion.** The key was already presented upstream. To cut them, use the GW29 model kill or isolation.

### 2.10 D4 (env-var keys refused in production), three layers

1. **Projector:** a row with `api_key_env_var` is published as `omitted: credential_env_var`. No key material or variable name enters state.
2. **Gateway apply:** the record schema has no field that names an environment variable. An entry carrying one is an unknown field, so it is omitted and counted (parent §6.2).
3. **Gateway boot:** in `AMF_PROFILE=production`, any set `AMF_PROVIDER_*` or `AMF_DEV_PROVIDER_*` variable, or any `OPENAI_API_KEY`-style variable in the closed provider list, **refuses to start**. Production code has no path that reads a provider key from the environment, and an AST gate keeps it that way.

A request for an omitted alias gets `model_unavailable` (503, `x-should-retry: false`; §3.8) with console rule `omitted:credential_env_var`. It never gets `model_not_found`.

### 2.11 Threat model

| Actor or event | Gets | Does not get, and why | Residual |
|---|---|---|---|
| Stolen state store dump (Valkey RDB/AOF, `amf_state_record` in PG, backups) | Org ids, aliases, base URLs, sealed DEKs and credentials, plans, KEY hashes | Plaintext keys: these need a KEK, which is never in state, control or PG | Metadata disclosure. Destroying a retired KEK on the fixed cadence makes old dumps permanently unreadable |
| Stolen gateway host (root, live) | Resident KEKs, the HMAC key, the cipher cache, in-flight plaintext. So **every provider key that gateway could open** | Write access to the state store (gateway ACL, §3.11). The control DB and the Fernet key | Inherent to D2: a data plane that opens keys without a call can be made to open them. Mitigations: no core dumps, not dumpable, no swap, non-root, read-only FS, KEK file `0400`; customers should use project-scoped provider keys with spend limits; later, KEK-per-cell sharding (Q8) |
| Stolen gateway disk or image | Nothing secret: KEKs are a mounted `_FILE` secret (tmpfs), not in the image | — | Cloud KMS later: the KEK file holds KMS-wrapped blobs, so a stolen file alone is useless |
| Compromised re-hydrator (holds the HMAC key and a writer Valkey user) | It can forge or regress records | Redirect a credential to a new `base_url` or org, or read one: both need a DEK (AAD binds `base_url`). Regression is refused by version and manifest anti-regress | It can deny service (fail closed). Ed25519 signatures (§7.8 follow-up) remove forging |
| Compromised projector | Control's Fernet key and the KEKs, so every key | — | Hardened like a gateway (§2.5 step 5). It runs on the control tier and never on the request path |
| Malicious org admin | Their own org's key, their own base URL | Other orgs' keys (AAD binds `org`; DEKs per org). Internal network reach (§4: `is_global` and the block list, private only through a staff-granted exception, resolve-then-pin, no redirects). Header injection (charset). Pool starvation of other orgs (per-(org, origin) in-flight cap, §3.6) | Their own upstream can stream hostile content, and the output scan still applies. Their own custom origins are capped per org |
| Replay or rollback of state | — | An old record is refused by warm workers (cursor), by manifest `not_before` and `feed_floor` (`state_sig.py:6-13`) and by the stamp. `v` in the AAD stops splicing an old ciphertext into a new record | A **cold** worker's single-record fetch: F1a-2 §6.2 must close it or state its bound. This plan inherits that bound |
| Log, trace or metrics leak | — | §2.7 gates plus the planted-key grep | — |
| Control or DB compromise | Fernet tokens; with the control host, also the model key | KEKs and DEKs | Out of scope (control hardening) |

### 2.12 Cost per request at 100k RPS (measured)

`aead_results.json`: Python 3.12.3, `cryptography` 46.0.3, OpenSSL 3.0.13. Each figure is thread CPU, the minimum over 7 repeats of the mean of 20k operations. The host load average was 1.8–7 from other agents.

| Operation | ns per op | Where |
|---|---|---|
| Credential decrypt, cached `AESGCM(DEK)` (39 / 164 / 256-byte keys) | 586 / 545 / 818 (locked run: 520 / 580 / 583) | request path, hit |
| `AESGCM(DEK)` built per call, then decrypt | 1,605–1,988 | request path, no cipher cache |
| Cold: KEK unwrap + build + decrypt | 2,106–2,445 | request path, cache miss |
| AAD encode (143 bytes) | 1,651 | apply path only (precomputed) |
| base64 decode of `n` and `ct` | 486–1,126 | apply path only (decoded once) |
| `b"Bearer " + key` | 56–61 | request path |
| Encrypt (projector) | 963–1,067 | projector, changed rows |
| Fernet decrypt (projector) | 11,786–14,640 | projector, changed rows |
| Memo digest `s` (projector) | 1,702–2,066 | projector, every row per round |
| Cipher cache entry | 2,424 bytes RSS | LRU sizing |

At 100k RPS fleet-wide, all misses: 100k × 2.4 µs = **0.24 cores**. All hits: **0.06 cores**. For comparison, dispatch on httpx costs **3,155 µs** per streamed request, and on aiohttp **361 µs** (§6.1). Credentials are therefore under 1% of dispatch CPU, and the design does not need a plaintext cache to scale.

### 2.13 KEK provider interface (D5)

```python
class KekProvider(Protocol):
    def kids(self) -> frozenset[str]: ...                                  # exported as amf_gateway_cred_kids
    def unwrap(self, kid: str, nonce: bytes, wrapped: bytes, aad: bytes) -> bytes: ...  # UnknownKid / InvalidTag
    def wrap(self, kid: str, dek: bytes, aad: bytes) -> tuple[bytes, bytes]: ...         # projector only
```

- **`FileKekProvider` (now).**
  - Reads `AMF_CRED_KEKS_FILE` as JSON: `{"keys": [{"kid": "k2026q4", "key": "<b64 32 B>"}]}`.
  - Refuses: a file readable or writable by group or other; a duplicate kid; a kid outside `[a-z0-9-]{1,32}`; a key that is not 32 bytes.
  - Builds one `AESGCM` per kid at boot.
  - The kid set is fixed for the process lifetime. Adding a kid is a rolling restart, which keeps the Publisher's series count fixed.
- **`EnvKekProvider` (dev and test only).** `AMF_CRED_KEKS` holds the same JSON. It is refused in production because environment variables leak through `/proc/*/environ`, `docker inspect` and child processes. This is owner question Q2, since D5 says "file/env".
- **`KmsWrappedKekProvider` (later).**
  - Each file entry is `{"kid", "kms": {"provider", "key_id", "blob"}}`.
  - At boot, each process calls KMS Decrypt once per kid with encryption context `{amf: kek, kid, namespace}`. That is **W × kids calls per host per rollout**, independent of orgs and RPS.
  - A KMS outage at boot refuses to start; running processes are unaffected.
- **`KmsDirectKekProvider` (HSM-only policies, small fleets).** The KEK never leaves the KMS. A DEK unwrap is one KMS call per org per worker, on the apply path, never on the request path. Its limit at 100k orgs is stated in Q1.

## 3. Dispatch

### 3.1 Components and flow

```
edge (verified, masked body; parent pipeline unchanged)
  └─ RoutedProvider.open_routed (GW28: one open site, pre-content fallback)      dispatch/routed_provider.py
       └─ per candidate: UpstreamDispatcher.open(request, entry)                  NEW dispatch/upstream_dispatch.py
            ├─ ProviderProfile.shape(openai_body, entry) -> wire body             NEW dispatch/upstream_profiles.py
            ├─ CredentialOpener.open(view, entry) -> header bytes                  NEW dispatch/cred_open.py
            ├─ OriginPools.lease(org, origin) -> pooled client + in-flight slot    NEW dispatch/upstream_pools.py
            │     └─ PinnedResolver (resolve-then-pin, cached, re-checked)         dispatch/url_guard.py (extended)
            └─ decode: SSEDecoder (CRLF, comments, usage, finish map) | JSON       edge/wire/sse_decode.py (extended)
```

Candidates:
- `RoutingTable` is built from the **pinned MODEL view** (F1a-2), not at boot.
- A candidate's `provider_key` becomes `(provider, origin)`.
- `RoutedProvider` keeps its single open call site. Its `clients[...]` lookup becomes `dispatcher.open(request, entry)`, and the entry is resolved from the pinned view by `candidate_id = row_id`.
- `rebind()` stays the only mutation of the verified body (`routed_provider.py:96-109`).
- Shaping happens **after** `rebind`, inside the dispatcher, and only ever **removes or renames** fields. It never adds text, so the verified text slots are untouched.

### 3.2 Provider profiles (OpenAI-compatible family)

Defaults were checked on 11 Nov 2026 against vendor documentation (links in `RESULTS.md` §5). Each row is pinned by a replayed capture test before merge (T-D4). Cells marked "(capture)" are unverified until then and default to *drop*.

| Profile | Default base | Auth | Budget field | Drops | Renames and maps | Stream usage |
|---|---|---|---|---|---|---|
| `openai` | `https://api.openai.com/v1` | Bearer | `max_tokens`. Reasoning models (ids starting `o<digit>` or `gpt-5`) take `max_completion_tokens` and drop `temperature`, `top_p`, the penalties and `logit_bias` | — | — | `stream_options.include_usage` |
| `google` | `https://generativelanguage.googleapis.com/v1beta/openai` | Bearer | `max_tokens` | `logit_bias`, `seed` (capture), penalties (capture) | `developer`→`system` (capture); `reasoning_effort` ∈ {low, medium, high} kept, `minimal`→`low` | `include_usage` (capture) |
| `mistral` | `https://api.mistral.ai/v1` | Bearer | `max_tokens` | `logit_bias`, `stream_options` (unknown fields are rejected) | `seed`→`random_seed`; `developer`→`system` | implicit (last chunk) |
| `deepseek` | `https://api.deepseek.com/v1` | Bearer | `max_tokens` | `logit_bias` (capture) | `developer`→`system` | `include_usage` |
| `huggingface` | `https://router.huggingface.co/v1` | Bearer | `max_tokens` | `logit_bias`, `reasoning_effort` (capture) | `developer`→`system` | `include_usage` (capture) |
| `meta` | `https://api.llama.com/compat/v1` | Bearer | `max_tokens` (capture) | `logit_bias`, `seed` (capture) | `developer`→`system` | (capture) |
| `cohere` | `https://api.cohere.ai/compatibility/v1` | Bearer | `max_tokens` | `logit_bias`, `n` (documented unsupported) | `reasoning_effort` kept only for `high` (`none`/`high` supported) | (capture) |
| `ollama` | none; operator-approved private base only (§4.3) | none, or Bearer if a key is set | `max_tokens` | `logit_bias` | `developer`→`system` | `include_usage` |
| `custom` | none; required | Bearer | `max_tokens` | `logit_bias`, `reasoning_effort` | `developer`→`system` | not requested |

Profile mechanics:
- **Profiles are data.** Each profile is one frozen value: allowed fields, renames, role map, budget rule, usage mode, first-event class, embeddings flag. Adding a provider is a table row plus its capture test.
- **The drop is the `drop_params` equivalent** (v1 `llm_router.py:685`). Each drop is counted in `amf_upstream_params_dropped_total{provider}`, never logged with a value.
- **Fields outside every profile can never reach shaping.** They are refused at parse with `unsupported_parameter` (`openai_params.py:100-134`).
- **Reasoning class.** The reasoning-model regex is profile data. An explicit control capability flag is owner question Q6, because §6.7 notes there is no `mode` column.
- **Budget (S1 F8 parity).** `upstream_body.py:167-176` already clamps. The profile only renames `max_tokens` to `max_completion_tokens` for reasoning models (v1/LiteLLM behaviour).
- **Streaming and non-streaming.** The upstream is always asked to stream chat (C10), so abort, first-event timing and incremental output scanning work the same for both client modes. The non-streaming route collects the stream, as today. A profile flag `stream_upstream=False` exists for a model that cannot stream; it takes the JSON path of §3.9 and is unused in the shipped family.
- **`include_usage`.** It is requested where the profile allows. The decoder lifts `usage` into a new `UpstreamEvent.usage`, and charging reconciles the estimate against it (closing the v2-enforcement-leaks finding 4 gap for streams that report usage). A client that asked for `include_usage` gets the usage chunk downstream.

### 3.3 `/v1/completions` (S1 F5)

- `prompt` is folded as `completion_prompt` and scanned (C9), and is sent upstream as one `user` message to the chat endpoint, because most of the family has no legacy completions endpoint.
- F1a-3 adds the L36-1 assertion: a `prompt` carrying a blocked value makes **zero** recorder calls, and a redacted one arrives masked.
- The response stays chat-shaped. Legacy `text_completion` parity is owner question Q7.

### 3.4 Upstream SSE fidelity (S1 F6, §7.6)

Decoder changes in `edge/wire/sse_decode.py`:
- **Framing.**
  - An event ends at `\n\n`, `\r\n\r\n` or `\r\r`, matching the WHATWG event-stream grammar.
  - Lines split on `\r\n`, `\n` or `\r`.
  - A comment-only event (`: ping`, `: keep-alive`, OpenRouter's `: OPENROUTER PROCESSING`) yields nothing and is **not** malformed.
  - An event with fields but no `data` stays malformed.
  - The test feeds every split point (Property 9 style) for each framing.
- **`finish_reason`.** The value is mapped per profile onto the closed set `stop | length | tool_calls | content_filter`. For example `model_length`→`length`, and `eos`→`stop` where a capture shows it. Anything else is malformed and counted.
- **`usage`.** Read from any chunk, and the last one wins, bounded to non-negative integers.
- **`reasoning_content` deltas** (deepseek, some hosts) arrive as the channel `reasoning_content`.
  - The wire layer treats that key as the reasoning channel (`edge/wire/tool_stream.py:70`), and the domain channel `output_reasoning` is declared scanned (`domain/channels.py:45`).
  - That every egress scan path scans a delta keyed `reasoning_content` has **not** been proven in this design pass.
  - T-D5 is the gate. If it fails, the decoder normalises vendor reasoning keys onto `output_reasoning` before egress.
- **Vendor `{"error": …}` mid-stream** keeps the GW12 R11 scan-then-forward path (`openai_upstream.py:20-21`).
- **Bounds.** An event over `max_upstream_event_bytes()` (contract), or a buffer that grows past it without a terminator, is `stream_malformed_upstream` (DoS bound).

### 3.5 Abort semantics per stream (S1 F3/F4)

These are kept, and tested at the new layer.
- Each request's upstream read is owned by its own async generator.
- A client disconnect, a kill-switch latch, a timeout or a fallback closes that generator. Closing it exits the response context and **closes the connection**: a half-read body is never returned to the pool.
- `abort()` stays a no-op (`openai_upstream.py:174-176`).
- T-D6:
  - two concurrent streams on one origin pool; cancel one; the other completes byte-identical;
  - the cancelled socket is closed (the pool count drops, and the recorder sees FIN within `cancellation_bound_s()`);
  - the same with the GW29 kill latch, and after a GW28 fallback.

### 3.6 Pools, limits and timeouts (§7.4)

**Pool table** (`dispatch/upstream_pools.py`, one per worker):
- **Key.** `Origin = (scheme, host, port)`, canonical (lower-case host, explicit port).
- **One pool per hostname origin, never shared across hostnames.**
  - httpcore reuses a pooled connection when the request's origin equals the connection's (httpcore 1.0 `_async/connection.py:167-168`). It reads `sni_hostname` only when it connects (`:107,151`).
  - A pin rewrites the request's host to an IP. If one pinned transport served two hostnames behind one CDN address, a request for one provider could therefore reuse a TLS session negotiated for the other's SNI.
  - With aiohttp (§6), a custom resolver keeps the hostname as the pool key natively.
- **Default-provider origins** (the profile defaults, at most 7) are created lazily and **never evicted**.
- **Custom origins** sit in a **refcounted LRU** of capacity `upstream_custom_origins()`, contract-derived from the FD budget (§6.2).
  - Eviction removes only an origin with zero in flight, and closes its idle connections.
  - If every custom origin is busy, a new one is refused: `upstream_unavailable`, fallback-eligible, counted in `amf_upstream_origin_refused_total{reason="table_full"}`.
- **Distinct custom origins per org** are capped by the projector (`omitted: base_url_cap`, contract `custom_origins_per_org()`) and re-checked when the view is applied.
- **Why not one pool per `(org, base_url)`.**
  - With 100k orgs on a handful of provider origins, that would multiply sockets and TLS handshakes by the org count and remove all connection reuse.
  - Isolation does not need it. Credentials never ride a pool (they are a per-request header), and the per-(org, origin) in-flight cap below bounds what one org can take from a shared origin.
  - The org dimension lives in the cap; the origin dimension lives in the pool.

**Client settings** (§7.4):
- `trust_env=False`;
- `follow_redirects=False`, or `allow_redirects=False` per request;
- an explicit pinned transport or resolver;
- **no default headers.** `Authorization` is set per request from §2.6, so a pool can never carry one org's key into another org's request.
- TLS: system CA bundle, verification always on, SNI is the hostname, TLS ≥1.2, `OP_NO_COMPRESSION`.
- **Boot assertion (A5).** Every pool's client is checked for no environment proxy, no redirects and no default `Authorization` header. A test then sets `HTTPS_PROXY` and asserts that the request still reaches the recorder directly.

**Capacity, from the contract and not from `pool_size(PROVIDER)` (fixes C4):**
- Every admitted in-flight request holds at most one upstream connection over HTTP/1.1, so the upstream slots per worker are `upstream_slots() = admission concurrency per worker` (`AdmissionBounds.concurrency`).
- Per-origin limit: default origins `= upstream_slots()`; custom origins `= ceil(upstream_slots() × custom_origin_share)`.
- Idle keep-alive per origin is capped at `limit / 2`, with `keepalive_expiry` 20 s. That is below the ≥60 s idle close that providers use, so a pooled connection is never reused after the server has dropped it.
- **Pool-acquire wait** = `min(remaining request deadline, upstream_first_event_timeout_s())`. A default origin never waits by construction. On expiry the code is `upstream_timeout` with rule `pool_wait`.
- **Per-(org, origin) in-flight cap:**
  - `ceil(origin_limit × org_origin_share)`, contract-derived, proposed ½ for shared default origins and 1 for an org's own custom origin;
  - a try-acquire that refuses at once with `upstream_inflight_limited` (429, `retry-after: 1`) rather than queueing;
  - entries exist only while in flight, so the dict is bounded by `upstream_slots()`.

**Timeouts** (fixes C13; all from the contract):

| Bound | Value | Notes |
|---|---|---|
| connect, including TLS | `upstream_connect_timeout_s()` | p99 × 250, 5 s at 20 ms |
| first event | `min(profile class, cap)` | standard 30 s (today's `upstream_first_event_timeout_s`); **reasoning class 120 s**, v1's ceiling (§7.6) |
| mid-stream gap | per class: standard 30 s, reasoning 60 s | **not** today's 0.8 s; amendment A4 |
| total | `upstream_total_timeout_s()` | 299.94 s |
| request write | `upstream_write_timeout_s()` | |

- The edge's `inter_chunk_timeout_s()` cut (`stream_timeouts.py:186-191`) is replaced, for routed chat streams, by the stall guard's per-class gap plus `cancellation_bound_s()`, so the router always fires first (GW28 D-13 ordering).
- Downstream, the gateway sends an SSE comment keep-alive (`: keep-alive`) every `downstream_keepalive_s()` while the upstream is quiet. Clients and load balancers then see bytes, and SDKs ignore comments.

**Retries and idempotency.**
- A chat or embeddings POST is **not idempotent**: provider cost, stored responses. So the HTTP layer **never retries**: no transparent retry on a reused connection, and connector retries are 0.
- The only re-attempt is GW28's candidate fallback, **pre-content only** (`routed_provider.py:69,284-286`), on this closed set:
  - `upstream_timeout`, `upstream_unavailable`, `upstream_rate_limited`, `upstream_error` (unchanged);
  - `provider_unreachable` and `upstream_destination_refused`, both pre-send, so nothing reached a provider;
  - `credential_unavailable` for that candidate, also pre-send.
- Fallback after `upstream_timeout` may double-charge the customer's provider account. That is today's GW28 behaviour, and the routing decision records it.
- v1's `num_retries=2` is deliberately not reproduced. That is the R6 retry-storm class (`llm_router.py:1819`).

### 3.7 Error mapping

Status first, then a bounded body classification:
- Read at most `max_provider_error_bytes()` (contract, 8 KiB) of a non-2xx body. It is used only to classify, never echoed or logged.
- Mappings:
  - 401 and 403 → `upstream_auth_error`.
  - **Google's 400 with `API_KEY_INVALID`** → `upstream_auth_error` (§7.6).
  - 402, and 429 with `insufficient_quota` (OpenAI's billing signal, DeepSeek's 402) → `upstream_quota_exceeded`, with `x-should-retry: false`. A billing failure must not be retried as a rate limit.
  - 429 → `upstream_rate_limited`, with the provider's `retry-after` clamped to `[1, max_retry_after_s()]` and passed through. This needs a new `UpstreamEvent.retry_after_s`; today `UpstreamEvent` carries no headers.
  - 400, 413 and 422 → `upstream_invalid_request`.
  - 404 → `upstream_not_found`.
  - 408 and 504 → `upstream_timeout`.
  - 500 → `upstream_error`.
  - 502 and 503 → `upstream_unavailable`.
  - 3xx → `upstream_error`, never followed.
- A 2xx that is not `text/event-stream` (chat) or not JSON (embeddings) is `stream_malformed_upstream`.
- `PinnedRefused` is caught **inside** the dispatcher (fixes C6) and becomes `upstream_destination_refused`.

### 3.8 Codes table (§7.5)

Every code below gets an `_ENVELOPE` row (`edge/errors.py:159-347`), a `failure_class` entry (`domain/failure_class.py:46-71`) and a `stages_to_console` test row.

| Code | HTTP / type | Retry headers | Class | Console (stage → action, rule) | New? |
|---|---|---|---|---|---|
| `upstream_auth_error` | 502 `gateway_error` | `x-should-retry: false` | operational | `model_output` → error, rule `upstream_auth_error` | reuse; header added |
| `upstream_quota_exceeded` | 429 `rate_limit_error` | `x-should-retry: false` | operational | `model_output` → error | **new** |
| `upstream_rate_limited` | 429 `rate_limit_error` | `retry-after` (clamped) | operational | `model_output` → error | reuse |
| `upstream_invalid_request` | 400 `invalid_request_error` | — | operational | `model_output` → error | reuse |
| `upstream_not_found` | 404 `not_found_error` | — | operational | `model_output` → error | reuse |
| `upstream_timeout` | 504 `timeout_error` | — | operational | `model_output` → error (rule `pool_wait` / `first_event` / `gap` / `total`) | reuse |
| `upstream_unavailable` | 502 `gateway_error` | — | operational | `model_output` → error | reuse |
| `upstream_error` | 502 `gateway_error` | — | operational | `model_output` → error | reuse |
| `upstream_request_unbuildable` | 500 `gateway_error` | — | operational | `model_output` → error | reuse |
| `stream_malformed_upstream` | 502 `gateway_error` | — | operational | `model_output` → error | reuse |
| `upstream_destination_refused` (PinnedRefused) | 502 `gateway_error` | `x-should-retry: false` | operational | `model_output` → error, rule `destination_refused` | **new** (§7.5 "PinnedRefused gets a declared code") |
| `provider_unreachable` | 502 `gateway_error` | — | operational (already in `PREDISPATCH_OPERATIONAL`) | `model_output` → error, rule `dns` | reuse: DNS resolution failure, pre-send |
| `upstream_inflight_limited` | 429 `rate_limit_error` | `retry-after: 1` | operational | `model_output` → error | **new** |
| `credential_unavailable` | 503 `service_unavailable` | — | operational (`PREDISPATCH_OPERATIONAL`) | `model_output` → error, rule `unknown_kid` / `dek_tag` / `cred_tag` | reuse |
| `model_unavailable` (D4 and every omission) | 503 `service_unavailable` | `x-should-retry: false` | operational | `model_routing` → error, rule `omitted:<reason>` | **new** |
| `model_not_found` / `routing_unknown_model` | 404 `not_found_error` | — | **policy** | `model_routing` → block | classify (§7.5) |
| `routing_internal_error` | 503 `service_unavailable` | — | operational | `model_routing` → error | classify (§7.5) |
| `unsupported_operation` (embeddings on a non-embedding profile) | 422 `invalid_request_error` | — | policy | `model_routing` → block | reuse |

- **Console stage convention** (unchanged from `console_stages.py:163-193`):
  - A refusal at routing preflight is drawn on `model_routing`. Preflight runs after the input decision and before `mark_dispatched` (`openai_chat.py:704-711`; `chat_stream.py:381`). That covers `model_unavailable`, `model_not_found` (already mapped there from `routing_unknown_model`) and `unsupported_operation`.
  - Every failure **after** it is `model_output` → error, with the code as the rule. That includes the dispatcher's pre-send failures: destination refused, DNS, in-flight cap, credential.
  - Pre-send failures carry a new `UpstreamEvent.sent=False` (§7), so the lifecycle refunds the budget hold. Nothing was sent, so nothing can be charged.
- **Omission reasons** (closed vocabulary, shared with F1a-2 §6.5): `credential_env_var`, `credential_missing`, `credential_invalid_format`, `credential_unreadable`, `base_url_refused`, `base_url_cap`, `provider_unsupported`, `alias_collision`.
- **The refusal keeps its candidate** (§7.5, fixes C7). `RoutedProvider._refusal` passes the candidate it failed at, so `RoutingDecision.selected_candidate` and `destination` are set on refusals.
- **`retry_headers` generalisation.** A `NO_RETRY_CODES` set (`upstream_auth_error`, `upstream_quota_exceeded`, `upstream_destination_refused`, `model_unavailable`) emits `x-should-retry: false` with no `retry-after`. Rate-limit codes emit `retry-after` only. Incident codes are unchanged.

### 3.9 Embeddings (§7.6)

- A non-SSE path: `UpstreamDispatcher.post_json(request, entry)` POSTs `<base>/embeddings` with `encoding_format: "float"` forced.
- It reads a body bounded by `max_embedding_response_bytes()` and validates `data[i].embedding` (finite float arrays, one per input, index-ordered, equal dimension).
- It yields the vectors as today's per-input delta events, so `deps.renderer.embeddings_body` is unchanged.
- Profile `embeddings` flag:
  - true for openai, google, mistral, cohere, ollama and custom (capture);
  - false for deepseek, huggingface (router; capture) and meta;
  - false means `unsupported_operation` before dispatch.
- Pre-send failures refund (they move above `mark_dispatched`, fixing the S1 §4a note). `upstream_usage` from the JSON body feeds charging.

### 3.10 Observability (§7.7)

- **Trace.** `pipeline_trace.routing = {requested_model, selected_model, route_destination}`.
  - `route_destination` is the **provider id** (closed vocabulary), never `base_url` or the origin.
  - `console_schema.py:51` gains `routing`, with its own key set and value grammars (alias grammar of §6.8; provider vocabulary).
  - The audit record carries it unchanged.
  - Control's ingest stores it (`v2_audit_ingest.py`), and the console shows it in the routing stage (frontend change, owner question Q10).
- **Series.** All pre-seeded, with closed label vocabularies: the D1 provider list (12), the code list of §3.8 and the omission list. They are added to `Publisher.SERIES_COUNT` with their own producer, like `UnauthAuditCounters`.

| Series | Labels (cardinality) |
|---|---|
| `amf_upstream_requests_total` | provider × outcome{ok,error} (24) |
| `amf_upstream_failures_total` (§7.7) | provider × code: the 14 dispatch codes of §3.8 (12 × 14 = 168) |
| `amf_upstream_first_event_seconds` (histogram) | provider (12 × 3) |
| `amf_upstream_params_dropped_total` | provider (12) |
| `amf_upstream_pool_connections`, `amf_upstream_pool_origins` | class{default,custom} (2 + 2) |
| `amf_upstream_pool_wait_seconds` (histogram) | — (3) |
| `amf_upstream_origin_refused_total` | reason{table_full,ssrf,org_cap} (3) |
| `amf_upstream_inflight_limited_total` | — (1) |
| `amf_model_omitted_requests_total` | reason (8) |
| `amf_cred_open_failures_total` | reason{unknown_kid,dek_tag,cred_tag,format} (4) |
| `amf_cred_cipher_cache_total` | event{hit,miss,evict} (3) |
| `amf_gateway_cred_kids` (§7.1) | kid: one per held KEK, fixed per process, ≤ `cred_max_kids()` = 4 |

- **Logs.** `routing_decision` gains `request_id=` from `decision.request_key` (§7.7; `routing_trace.py:205-217`). Dispatch logs carry the request id, provider, origin class (`default`/`custom`, never the host), code and exception type name.
- **L36-1 metric deltas** are read from a single-worker gateway (§7.7): the Publisher is per worker.

### 3.11 Trust boundary (§7.8) and compose `gateway-v2-prod` (§7.9)

**Valkey ACL**, on a dedicated `valkey-state` service for the production profile. The shared dev `redis` (open on `0.0.0.0:6379`, C17) is unchanged.
- `user default off`.
- `user gateway`:
  - **read-only** (Redis 7 `%R~`) on `{ns}:meta:*`, `{ns}:idx:*`, `{ns}:on:*`, the hash-stored kinds `{ns}:key`, `{ns}:ks` and `{ns}:budget` (`HASH_KINDS`, `store_keys.py:31`), the key-stored `{ns}:plan:*` and `{ns}:model:*`, and `{ns}:stamp`;
  - `SUBSCRIBE` on `{ns}:updates` (`&` channel rule);
  - read-write (`~`) only on the request-path families `{ns}:budget:*` (the per-org counters, distinct from the `{ns}:budget` state hash), `{ns}:audit:*`, `{ns}:auditctl:*`, `{ns}:responses:*` and `{ns}:fleet:kids:*`.
- `user projector` and `user rehydrator`: write on the state families.
- Passwords come from `_FILE` secrets.
- Key families: `runtime/store_keys.py:44-177`.
- T-S1 asserts that the gateway user gets `NOPERM` on `SET {ns}:model:x`.
- Ed25519 record signatures remain the named follow-up (§7.8). With them, a gateway holds only the public key, and the "compromised gateway forges state" row of §2.11 disappears.

**`gateway-v2-prod`**, compose profile `gateway-v2-prod`, so it is not started by default:
- **Command:** `python -m gateway_v2.edge.serve`, with `AMF_PROFILE=production`.
- **Secrets (`_FILE`)** under `/run/secrets`: `AMF_STATE_HMAC_KEY_FILE`, `AMF_CRED_KEKS_FILE`, `AMF_STATE_VALKEY_PASSWORD_FILE`. Production refuses the plain env forms of these.
- **Limits:**
  - `ulimits: {core: 0, nofile: 65536}`;
  - `AMF_FD_LIMIT: "65536"` (stated, `runtime/cgroup.py:242`);
  - `mem_limit` equal to `memswap_limit`.
- **Container hardening:** `user: "10001:10001"`, `read_only: true`, a `tmpfs` for `/tmp`, `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`.
- **Port** `127.0.0.1:8302:8302`.
- **Workers:** `WEB_CONCURRENCY: ${GATEWAY_V2_PROD_WORKERS:-1}`. L36-1 runs one worker.
- **`depends_on` healthy:** `valkey-state`, `state-rehydrator-a`, `state-projector-a`.
- **Healthcheck:** `/readyz`.
- **`AMF_PRIVATE_EGRESS_CIDRS`:** empty by default.
- **Test overlay.** `docker-compose.f1a3-test.yml` is the **only** place the recorder is reachable (§7.3).
  - It adds the T02 recorder on a private network.
  - It sets `AMF_PRIVATE_EGRESS_CIDRS` to that network.
  - It seeds an `OrgPrivateEgress` row for the test org.

## 4. SSRF (§7.3)

### 4.1 Base-URL grammar

The grammar is checked at the projector (omit `base_url_refused`), re-checked at gateway apply, and the final URL is asserted at dispatch.
- **Scheme:** `https`. `http` only for an org-scoped private exception (§4.3).
- **Host:** lower-case LDH labels, each ≤63, total ≤253, no trailing dot; or a dotted-quad IPv4 or bracketed IPv6 literal.
  - **Refused:** userinfo, percent-encoding in the host, IPv6 zone ids, IDN (punycode is accepted as LDH), and all-numeric or hex shortcuts (`2130706433`, `0x7f.1`, `127.1`).
- **Port:** 1–65535. The default port is omitted in the canonical form.
- **Path:**
  - only `[A-Za-z0-9._~!$&'()*+,;=:@/-]` segments;
  - **refused:** `.` and `..` segments, `%2e`, `%2f`, `%5c`, backslashes, empty segments;
  - no query and no fragment.
- **Join:** paths are joined **structurally**: `base_path + "/" + endpoint` with `endpoint ∈ {chat/completions, embeddings}`. The dispatcher asserts that the final URL's path equals `<base path>/<endpoint>` before dialling.
- **F1b/F1c:** the same grammar governs `api_version`, Bedrock `region` and every path segment.
- **Host allowlist.**
  - Default origins are the profile table (§3.2).
  - Custom **public** hosts are not restricted by default, because a customer's own vLLM, OpenRouter or Together endpoint is a legitimate target.
  - An operator may set `AMF_CUSTOM_HOST_ALLOW`, a list of exact hosts or `*.suffix` patterns. Both the projector (omit `base_url_refused`) and the gateway (refuse at connect) then enforce it. This is Q12.

### 4.2 Address policy and resolve-then-pin

An address is **refused** when it is not `is_global` **or** `_blocked(...)` refuses it (§7.3).
- `_blocked` refuses metadata IPs, link-local, loopback, reserved, multicast and unspecified addresses (`url_guard.py:203-228`).
- **Embedded IPv4 is decoded and re-checked, unconditionally, even for an allowlisted host:**
  - `64:ff9b::/96` (low 32 bits);
  - `64:ff9b:1::/48` (RFC 8215, low 32 bits);
  - `2002::/16` (`sixtofour`);
  - Teredo `2001::/32` (`teredo` client);
  - `::/96` (IPv4-compatible);
  - `::ffff:0:0/96`.
- This closes the accident in C5 by design.
- Evidence: `ssrf_probe_py312.json`. T-S2 runs the table both ways (default and allowlisted).

Resolve-then-pin (`PinnedResolver`, one per origin):
- **Resolve once per TTL window** (`upstream_dns_ttl_s()`, proposed 30 s) through the loop's non-blocking resolver.
- **Check every returned address.** Any refusal refuses the whole answer, so a mixed public and private answer is refused (`url_guard.py:284-287`).
- **Dial only checked addresses.** A new connection uses the cached checked set and never resolves again. Existing connections stay on the address they were checked against.
- **Expiry.** When the window expires, resolve again and check again. A changed answer that is now refused makes new connections raise `PinnedRefused`, which becomes `upstream_destination_refused`.
- **Cost.** Per-request resolution, today's behaviour (C5), would add a `getaddrinfo` executor hop to every request: **≈68 µs CPU** measured. The cache makes it amortised O(1).
- **DNS rebinding defence** = check-on-resolve + dial-the-checked-address + no second resolution + bounded TTL. T-S3 uses a resolver double that answers public, then `127.0.0.1`, then `169.254.169.254`.

### 4.3 Private targets (owner-configurable exception for local ollama)

A private, non-global target is dialled only when **both** hold:
1. **The org holds a staff-granted exception.** It is an `OrgPrivateEgress(org, scheme, host, port)` row, created only by platform staff in control and projected into the org's MODEL record `egress`. The exception is per org: org B cannot use org A's exception. It is enforced at the projector, so a non-matching entry is omitted.
2. **The address is inside the operator fence** `AMF_PRIVATE_EGRESS_CIDRS` on the gateway. The default is empty, meaning no private egress at all. A holder of the HMAC key who injects an `egress` entry still cannot reach a private network the operator did not fence in.

Further rules:
- These are **re-checked at connect**, against the resolved address, not the name.
- `http` is allowed only for such an entry (§7.3).
- Metadata, link-local, loopback and multicast addresses are **never** allowed, exception or not. A local ollama on the docker host is reached through the host-gateway bridge address, which is private, not loopback.
- **Redirects are off everywhere.** A 3xx is `upstream_error`, and its `Location` is never read.

## 5. What the projector and the gateway each refuse, and when

| Check | Projector (omit) | Gateway apply | Gateway connect |
|---|---|---|---|
| D4 env-var key | `credential_env_var` | unknown field → omit | — |
| Key charset | `credential_invalid_format` | — (sealed) | header validity (client library) |
| Base-URL grammar | `base_url_refused` | re-check → omit | final-path assertion |
| Address policy | name-level only (literal IPs checked) | — | **resolve-then-pin, every new connection** |
| Private exception | `egress` row must exist | `egress` set in view | exception **and** CIDR fence |
| Custom origins per org | `base_url_cap` | count re-check | table capacity (§3.6) |

## 6. Scaling to 100k RPS (hardware-independent)

### 6.1 What was measured

The scripts and raw output are in `docs/plans/evidence/2026-11-11-f1a3-dispatch/` (`RESULTS.md`).

Setup:
- The client runs in its own process. It is the gateway-side cost, measured as `process_time` across all threads.
- The upstream is a separate pre-rendered keep-alive process (`upstream.py`). It sends 20 content chunks, a finish chunk, a usage chunk and `[DONE]`, with chunked transfer encoding and one HTTP chunk per SSE event.
- Concurrency 32, 1,500 requests × 2 repeats after a 200-request warm-up (`client_results.json`), plus a sweep over 1, 8 and 128 in flight (`client_concurrency.json`).
- **Run unlocked.** The full-size run under the shared CPU lock waited 52 minutes behind more than 15 jobs and was withdrawn. The reduced run used 70 s of CPU in total, under the guide's 2-minute lock threshold.
- The load average from other agents was 3.4–5.6, so treat differences under about 15% as noise. The ratios below are far outside that.

| Variant (each a full request: send, read every byte, close) | Client CPU µs/request, median (runs) | Upstream CPU µs/request | Errors |
|---|---|---|---|
| `current`: the shipped `OpenAIUpstreamProvider` (httpx) + `SSEDecoder` | **3,190** (3,267 / 3,112) | 117 | 0 |
| `design`: the §3.6 shape on httpx (`trust_env=False`, `PinnedTransport`, per-request decrypt into the header) + `SSEDecoder` | **3,155** (3,213 / 3,098) | 127 | 0 |
| `design_json`: the same, non-streaming JSON | 2,637 (2,530 / 2,744) | 127 | 0 |
| `httpx_raw`: httpx stream, bytes only | 2,670 (2,843 / 2,497) | 110 | 0 |
| `httpx_json` | 2,334 (2,355 / 2,314) | 110 | 0 |
| `aiohttp_raw`: aiohttp stream, bytes only | **204** (213 / 194) | 23 | 0 |
| `aiohttp_decode`: aiohttp + the real `SSEDecoder` | **361** (354 / 369) | 27 | 0 |
| `aiohttp_json` | 135 (143 / 127) | 20 | 0 |
| `design_tls`: `design` over TLS 1.3, pooled connection | 3,289 (3,529 / 3,049) | 183 | 0 |
| `tls_handshake`: `design` over TLS, keep-alive off (a full handshake per request) | 3,347 (3,186 / 3,508) | 730 | 0 |
| `getaddrinfo`, one call on the loop | 68.3 µs per call | — | — |
| Pooled connection RSS (httpx, 256 connections) | plain 22,240 B, TLS 36,416 B | — | — |

Concurrency sweep (`client_concurrency.json`): client CPU µs per streamed request.

| In flight | `design` (httpx) | `httpx_raw` | `aiohttp_raw` |
|---|---|---|---|
| 1 | 2,008 | 1,491 | 137 |
| 8 | 1,922 | 1,587 | 212 |
| 32 | 3,155 | 2,670 | 204 |
| 128 | 5,002 | 5,321 | 150 |

What the numbers say:
- **httpx is the dominant cost of dispatch, and it grows with concurrency.** The shipped provider and the design shape on httpx both cost ≈3.2 ms of CPU per streamed request at 32 in flight, rising to ≈5 ms at 128. A production worker holds hundreds of streams (§6.2).
- **aiohttp is flat.** It costs 0.14–0.21 ms at every concurrency, and 0.36 ms with the real decoder. That is **≈9× less** than the httpx design shape at 32 in flight, and **≈35× less** at 128 (raw against raw).
- **The decoder costs ≈160 µs** for a 23-event stream (`aiohttp_decode` − `aiohttp_raw`), about 7 µs per event. It is worth optimising later, but it is not the bottleneck.
- **Credentials cost 0.55–2.4 µs** (§2.12): under 1% even of the aiohttp path.
- **TLS.** A handshake added ≈550 µs of upstream CPU per request (730 against 183 µs). On the client the difference stayed inside httpx's own noise. Pooled keep-alive (§3.6) makes handshakes per origin, not per request.
- **Resolve-per-request** would add ≈68 µs of CPU plus an executor hop to every request (C5). The per-origin resolution cache removes it.

### 6.2 Per-worker budgets

A worker is one process on one core (`ResourceContract.workers()` = cores × 0.75, `resources.py:305-318`).
- **CPU per request** = `c_pipeline` (scan, plan and audit; measured by F1d) + `c_dispatch` (§6.1) + `c_cred` (≤2.4 µs).
- **Requests per second per worker** ≤ `0.75 / c_req` (the utilisation cap).
- **In flight per worker** = `RPS_w × T_upstream` (Little's law). `T_upstream` is the provider's mean stream duration, typically seconds, and it dominates. 500 RPS per worker at 3 s each is 1,500 concurrent upstream streams on that worker.
- **Memory per worker** = in-flight × (upstream connection + downstream connection + per-request state + stream buffer) + the cipher LRU (≈10 MB) + the pool table. Measured per pooled connection on httpx: plain 22,240 B, TLS 36,416 B (§6.1). So 1,500 TLS streams hold ≈55 MB of upstream connections; aiohttp's figure is measured with the implementation. The per-stream buffer is `stream_buffer_bytes()`.
- **FDs per worker** = 2 × in-flight + idle keep-alive. The admission concurrency per worker must satisfy `2 × concurrency + Σ keepalive ≤ connection_budget()`. F1a-3 adds that check to `derive_bounds` at boot: it refuses to start, with no silent cap. Nothing checks it today. With `AMF_FD_LIMIT=65536`: `connection_budget() = (65536 − W) × 0.75 ≈ 49k`, i.e. ≈24k in flight per worker, so FDs are never the binding limit before CPU.
- **TLS handshakes** cost ≈550 µs of CPU on the upstream side (§6.1; the client side stayed inside httpx's noise). With pooled keep-alive (`keepalive_expiry` 20 s) the handshake rate is bounded by `origins × limit / 20 s` per worker, not by RPS.

### 6.3 What this means for 100k RPS

- **Fleet CPU for dispatch alone** = 100k × `c_dispatch`: **≈316 cores** with httpx, against **≈36 cores** with aiohttp (§6.1 medians at 32 in flight). Credentials add 0.06–0.24 cores.
- **Per worker, before any pipeline work**, dispatch alone caps a worker at `0.75 / 3.155 ms` ≈ **240 RPS** on httpx, against `0.75 / 0.361 ms` ≈ **2,080 RPS** on aiohttp. httpx's cost also grows with in-flight concurrency, which a worker serving seconds-long streams always has.
- **Recommendation.** Dispatch sits behind one `UpstreamTransport` seam (`post_stream`, `post_json`, `aclose`), with **aiohttp as the production implementation** (`TCPConnector` with a custom `AbstractResolver` that does resolve-then-pin while keeping the hostname for SNI, the pool key and `Host`; `trust_env=False`; `allow_redirects=False`).
  - httpx stays for GW32 OAuth (`pinned_http`) and the tests.
  - This is owner question Q5: it adds aiohttp and its pinned, hashed dependencies to the image.
  - The rest of the design is client-independent.
- **HTTP/2 is not adopted.** Multiplexing would cut sockets and TLS handshakes, but FDs are not the binding limit (§6.2), and h2's pure-Python framing adds CPU per request. It can be revisited with F1d's measurements.
- **Every bound is per worker and derived from the contract.** Throughput scales with workers × cores × hosts. The only shared, fleet-level costs are:
  - KMS calls (once per process per kid);
  - the projector round, measured in parent §6.6 at 100k orgs × 5 models;
  - Valkey reads (unchanged by this slice; F1d shards them).
- **Not claimed here:** the fleet curve. F1d measures `c_pipeline` and the end-to-end knee.

## 7. Interface between edge and dispatch

- `UpstreamRequest.body` keeps `openai_body` and `openai_endpoint` (`upstream_body.py:105-111`).
- `UpstreamEvent` gains three optional fields, so the existing producers are unchanged:
  - `usage: Usage | None`;
  - `retry_after_s: float | None`;
  - `sent: bool = True`: `False` on a pre-send failure, so the lifecycle refunds (§3.8).
- `RoutingContext` gains nothing. The candidate's entry comes from the pinned MODEL view, which `RoutedProvider` receives through the existing `snapshot_source`-style injection (`routing_boot.py:109-149`). The table is built per view version, not at boot.

## 8. Test plan

Offline (house idiom: seeded `random.Random`, ≥10,000 iterations for properties):

| Id | Test |
|---|---|
| T-C1 | AAD encoding is injective. Property: random tuples with separators and empty fields never collide. A known-answer vector is pinned |
| T-C2 | A round trip opens. Each AAD field changed by one byte → `InvalidTag`, for every field |
| T-C3 | A credential sealed for org A, placed in org B's record → `credential_unreadable` for B and A untouched (AAD and DEK both) |
| T-C4 | Reuse rule: unchanged token and AAD → byte-identical `{v,n,ct,s}`. Any change → `v+1` and a fresh nonce. 10k random edits never repeat a `(DEK, nonce)` pair |
| T-C5 | KEK rotation simulation: gate refuses until every heartbeat lists the kid; re-wrap backlog bounded per round; retire refused while a record or a cursor lags |
| T-C6 | DEK rotation is atomic per record; requests during apply succeed with old or new, never fail |
| T-C7 | Unknown kid, DEK tag failure and credential tag failure → the parent §6.2 markers. Other orgs unaffected. Readiness unchanged |
| T-C8 | D4: env-var row omitted at the projector; an unknown env field in the record is omitted at apply; a production boot with a provider env key refuses |
| T-C9 | `FileKekProvider` refusals: mode, duplicate kid, length, grammar. `EnvKekProvider` refused in production. Boot KAT |
| T-L1 | Planted-key grep across every sink listed in §2.7 |
| T-L2 | AST gates: type gate (§2.7); credential-flow scope; no `str(exc)` in dispatch logs |
| T-D1 | Profile shaping table: for each profile, the input field set maps to exactly the expected wire set (drops, renames, role map, reasoning budget rename) |
| T-D2 | Shaping never changes or adds a text value. Property over random verified bodies: the multiset of strings is unchanged except removed fields |
| T-D3 | SSE decoder: LF, CRLF and CR framing; comments; every split point (Property 9); `finish_reason` map; `usage`; oversized event; `reasoning_content` |
| T-D4 | **Replayed vendor captures** per profile (checked into `tests/fixtures/upstream_captures/<provider>/`, scrubbed): streaming, tool calls, usage, error events |
| T-D5 | A planted value in `reasoning_content` is redacted before forwarding |
| T-D6 | Abort isolation (§3.5), including the kill latch and post-fallback |
| T-D7 | Codes: every §3.8 code renders its row, its retry headers and its console stage. `stages_to_console` table test |
| T-D8 | Error classification: Google `API_KEY_INVALID`, `insufficient_quota`, 402, 429 with `retry-after` (clamped), 3xx not followed |
| T-D9 | Pools: one pool per hostname even when two names pin to one IP (asserted on the pool table and on SNI); default origins never evicted; custom LRU evicts only idle; refcount; table-full refusal; per-(org, origin) cap; pool-acquire bounded by the deadline |
| T-D10 | Retries: no HTTP-level retry (recorder counts exactly one call per attempt); fallback only on the closed set and only pre-content |
| T-D11 | Embeddings JSON path: shape validation, refund on pre-send failure, `unsupported_operation` profiles |
| T-S1 | Valkey ACL: the gateway user gets `NOPERM` writing a state family and succeeds on budget, audit and responses |
| T-S2 | SSRF address table (`ssrf_probe.py` rows, plus all IANA special-purpose blocks) refused by default and still refused when allowlisted, for every embedded-IPv4 form |
| T-S3 | DNS rebinding: resolver double public → loopback → metadata. Connections stay pinned, the next window is refused, and no socket is opened to a refused address (recorded by a fake transport) |
| T-S4 | Base-URL grammar corpus (≥200 hostile URLs). Final-path assertion. Redirects off |
| T-S5 | Private exception: needs both the org row and the CIDR fence; org B cannot use org A's exception |
| T-P1 | Gap bounds: a 5 s mid-stream pause completes (today it is cut at 1.0 s); a reasoning-class 90 s first event completes; a 130 s first event → `upstream_timeout` |

Fault injection (recorder modes, extending `staging/t02/recorder.py`):
- statuses: 401, 403, 402, 429 with `retry-after`, 500, 503, and Google-400;
- malformed event, CRLF framing, comment keep-alives, mid-stream pause, first-event delay;
- disconnect mid-stream, no `[DONE]`, oversized event, 302 to `169.254.169.254`;
- slow headers, a huge error body, an unknown `finish_reason`, a reset on a reused keep-alive connection;
- per-profile strict mode: Mistral-style 422 on an unknown field.

Live, **L36-1 exit** (parent §1), on the compose `gateway-v2-prod` with one worker and the test overlay:

| Step | Expect |
|---|---|
| 1 | Seed one org per shipped profile (`api_base` → recorder path `/p/<profile>`, recorder in strict per-profile mode). Valid console key, streaming and non-streaming → 200 completion. The recorder shows the shaped body, the bearer, and the per-run nonce exactly once |
| 2 | Invalid console key → 401 `invalid_api_key`; the unauth counter +1 (parent §5.5) |
| 3 | Blocked prompt (chat, and `/v1/completions` `prompt`) → declared refusal; recorder calls **0** |
| 4 | Invalid **provider** key (recorder 401) → 502 `upstream_auth_error`, `x-should-retry: false`; console row `model_output` error |
| 5 | Trace (`amf.trace` / HTTP console), metrics delta (`amf_upstream_requests_total{provider,ok}` +1 per request, `amf_upstream_failures_total` +1 for step 4) and the console row all agree, joined by request id. The request id is joined to the recorder by the nonce |
| 6 | Planted-key grep (T-L1) over everything the run produced |
| 7 | D3/revocation: delete the key in the console; time until the gateway serves `model_unavailable` ≤ 21 s (record the measured value) |

Acceptance, all required:
- T-* green;
- L36-1 steps 1–7 green;
- the full gateway_v2 suite and the control suites (ratchet) green;
- CI green;
- the §6 benchmark re-run on the implementation within 20% of the prototype row for the chosen client (`aiohttp_decode`, or `design` if Q5 keeps httpx), or the regression explained. The aiohttp resolve-then-pin resolver was not prototyped here; its cache-hit path is a dict lookup;
- two agent reviews.

### Rollout and rollback

1. **Control release** (independent):
   - the `_FILE` model key and the production refusal of hash-derived keys;
   - the separate `GatewayAPIKey` key with its dual-read migration;
   - the `OrgPrivateEgress` table.

   Rollback: the dual-read migration keeps the old key readable for one release.
2. **Gateway release.**
   - KEK file mounted; `amf_gateway_cred_kids` exported.
   - Dispatch code present.
   - MODEL applied only behind F1a-2's flags and rollout floor (parent §6.1). Without MODEL the provider stays the stub, which fails closed.

   Rollback to any image at or above F1a-2's step-1 floor is safe. Sealed credentials in state are inert to an image that does not apply MODEL.
3. **Projector:**
   - `AMF_CRED_ENCRYPT_KID` set; the kid gate passes; MODEL emitted (F1a-2 flag).

   Rollback means stopping the projection. F1a-2's rule that forbids rolling back a re-hydrator after step 2 applies unchanged.
4. **L36-1 live run**, then promotion.

**KEK incident.** Stop the projector, rotate the KEK and every DEK, and ask customers to rotate their provider keys. Cryptography cannot un-leak a key that was exposed while a KEK was compromised; the runbook says so.

## 9. Interface needed from F1a-2

F1a-3 consumes exactly the following and provides `cred_seal` / `cred_open`. Field names are proposals for the F1a-2 author to accept or rename.

| # | Needed from F1a-2 | Parent § |
|---|---|---|
| I1 | MODEL kind, key-stored `{ns}:model:<org>`, one record per active org (possibly empty) and a tombstone on offboard | §6.5, §6.11 |
| I2 | Record body carries the `dek`, `models[].cred`, `models[].row/alias/provider/base_url/model` and `egress` fields **as raw strings** (§2.4). F1a-2 owns the rest of the schema | §6.7 |
| I3 | Applier calls `CredentialOpener.check_org(record)` at apply time and maps its result onto the `unavailable(version)` marker or a per-entry omission | §6.2 |
| I4 | Pinned per-request `ModelView` with:<ul><li>`org_id`;</li><li>`version`;</li><li>`lookup(alias) -> ModelEntry \| OmittedEntry \| None`: O(1), casefolded;</li><li>`sealed_dek`;</li><li>`unavailable: str \| None`;</li><li>`egress`;</li><li>`entries_for_routing()`: the candidate list for one alias.</li></ul>Pinned with the plan before phase 4b | §6.3, §6.9 |
| I5 | `ModelEntry` exposes the sealed credential (opaque `SealedCredential`, built by `cred_open` at apply time with the precomputed AAD) and never plaintext | §6.4 |
| I6 | `OmittedEntry.reason` from the shared closed vocabulary of §3.8 | §6.5 |
| I7 | Projector round hooks: `seal_rows(org, rows, previous_record) -> body fragment` called inside the round. It respects the pre/post-heartbeat split, and the re-wrap/rotation backlog limit per round | §6.6 |
| I8 | A projector-private memo may use `s` (§2.4); its digest scheme replaces "a digest of the Fernet token" | §6.6 |
| I9 | The worker's applied MODEL `feed_seq` (the synchroniser cursor), readable by the dispatch layer so the fleet heartbeat `{ns}:fleet:kids:<gateway_id>` can carry it for the KEK retire gate | §6.10 |
| I10 | Breaker (§6.6) counts the omission reasons of §3.8 | §6.6 |
| I11 | The candidates-per-alias number (§6.7) bounds `routing_max_candidates()` for the view-built table | §6.7 |

## 10. Requirements I think need amending (with evidence)

| # | Requirement | Issue | Proposal |
|---|---|---|---|
| A1 | §7.1: the AAD tuple ends in `kid`; the reused triple is `{kid, nonce, ct}` | The owner asked for a KEK → per-org DEK → credential hierarchy (D2 direction, D5). With a DEK, putting the KEK `kid` in the credential AAD would force re-encrypting every credential on KEK rotation | The `kid` rules apply to the DEK wrap; the credential AAD binds `dek_id` (and so the kid transitively), plus `upstream_model` and `v`. The reused tuple is `{v, n, ct, s}` (§2.3) |
| A2 | §7.6: "Inactive and keyless rows are omitted" | Ollama is keyless by default (control lists it as "Ollama (Local)", `core/models.py:1553`), and D1 ships ollama | Keyless is allowed **only** for an entry whose base URL is covered by a staff-granted private exception (§4.3). Everything else keyless is `credential_missing`. Keyless Bedrock stays refused (§7.6) |
| A3 | §7.5: reuse `provider_unreachable` | The chat path already has `upstream_unavailable` for connect and read failures (`openai_upstream.py:60`). Two codes for one condition would split dashboards | `provider_unreachable` = DNS resolution failure (pre-send, nothing reached a provider). `upstream_unavailable` = connect or read failure after a destination was approved |
| A4 | Missing from §7: mid-stream bounds | Measured contract: gap 0.8 s; the edge cuts after 1.0 s without an upstream event (C13). Real model streams pause longer, so this would cut legitimate completions | Per-class gap bounds (30 s / 60 s), the edge cut subordinated to the stall guard, and downstream SSE keep-alives (§3.6). Test T-P1 |
| A5 | Missing from §7: proxy environment | The shipped client honours `HTTPS_PROXY` (C3) | `trust_env=False` is in §7.4; this plan adds the boot assertion and a test that `HTTPS_PROXY` in the environment changes nothing |

## 11. Open owner questions

| # | Question | Proposal |
|---|---|---|
| Q1 | **KEK custody in production** (local `_FILE` from a secret manager, cloud KMS wrapped keyring, or HSM-direct), who holds break-glass copies, and the rotation cadences | Wrapped keyring in the cloud KMS of the deployment; break-glass split between two owners; KEK 90 days, DEK 30 days, destroy a KEK one cadence after retirement. HSM-direct is not viable at 100k orgs × workers on the apply path |
| Q2 | D5 says "local file/env KEK". May production refuse the env form? | Yes: file only in production; env only in dev and test (§2.13) |
| Q3 | Keyless local ollama (A2) | Accept A2 |
| Q4 | Who may grant private-egress exceptions? | Platform staff only (§4.3), never an org admin |
| Q5 | **HTTP client for dispatch:** aiohttp (adds a dependency; measured ≈9× less CPU per streamed request at 32 in flight and ≈35× less at 128) or stay on httpx | aiohttp behind the `UpstreamTransport` seam; httpx kept for OAuth and tests |
| Q6 | Reasoning-model class by model-name pattern (profile data) or an explicit control capability column | Pattern now; a column later if customers bring custom reasoning models |
| Q7 | Legacy `/v1/completions` response shape (`text_completion`) in F1a-3, or later | Later (GW34 client matrix); the prompt is scanned now |
| Q8 | Blast radius of a gateway-host compromise: KEK-per-cell sharding | A later slice (F1d+); the hierarchy already supports it |
| Q9 | Per-org share of a shared provider origin per worker (`org_origin_share`) | ½ |
| Q10 | Console UI for `pipeline_trace.routing` | Schema and ingest in F1a-3; UI in the console track |
| Q11 | 402 and `insufficient_quota` → `upstream_quota_exceeded` (429, no retry) | Accept |
| Q12 | Should custom public hosts be restricted by an operator allowlist (`AMF_CUSTOM_HOST_ALLOW`) by default? | Unrestricted by default (customer endpoints), with the allowlist available. A regulated deployment sets it |

## 12. Traceability: §7 → this plan

| §7 item | Section |
|---|---|
| 7.1 AES-256-GCM, fresh `os.urandom(12)` nonce | §2.2, §2.3 |
| 7.1 AAD canonical length-prefixed `(namespace, org, row_id, alias, provider, base_url, kid)` | §2.3 (A1) |
| 7.1 reuse the triple only when plaintext and AAD are byte-identical | §2.3, §2.5 step 3, T-C4 |
| 7.1 explicit encrypt kid; switch gated on `amf_gateway_cred_kids{kid}` | §2.3, §2.8, §3.10, T-C5 |
| 7.1 retire a kid only when unused and the cursor has passed the re-wrap | §2.8, I9 |
| 7.1 fixed rotation cadence | §2.8, Q1 |
| 7.1 projector hardened, `_FILE` secrets | §2.5 step 5, §3.11 |
| 7.1 separate control key for `GatewayAPIKey` | §2.5 |
| 7.1 Fernet hash-derivation refused in production | §2.5 |
| 7.2 no dataclass protocol / AST gate | §2.7, T-L2 |
| 7.2 key charset; omit `credential_invalid_format` | §2.5 step 1 |
| 7.2 exception type names only | §2.7, §3.10 |
| 7.2 planted-key grep | §2.7, T-L1, L36-1 step 6 |
| 7.3 `is_global` **or** `_blocked` | §4.2, T-S2 |
| 7.3 embedded IPv4 decoded and re-checked | §4.2, T-S2 |
| 7.3 private targets only via per-org entries, projector and connect | §4.3, §5, T-S5 |
| 7.3 userinfo, query, fragment, backslash, dot segments rejected | §4.1, T-S4 |
| 7.3 structural join; final-path assertion | §4.1 |
| 7.3 same grammar for `api_version`, region, segments (F1b/F1c) | §4.1 |
| 7.3 recorder only through a test overlay | §3.11 |
| 7.3 `http` only for org-scoped private entries | §4.1, §4.3 |
| 7.4 pools keyed by origin; default origins never evicted | §3.6, T-D9 |
| 7.4 custom origins per org capped | §3.6, §5 |
| 7.4 pool-acquire bounded by the request deadline | §3.6 |
| 7.4 `trust_env=False`, `PinnedTransport`, `follow_redirects=False`, no default auth header | §3.6 (A5) |
| 7.4 per-org in-flight cap per origin | §3.6, T-D9 |
| 7.4 refcounted LRU pool table | §3.6, T-D9 |
| 7.5 a row per code with status, type, class, console | §3.8, T-D7 |
| 7.5 `upstream_auth_error` 502 with `x-should-retry: false`; `provider_unreachable` | §3.8 (A3) |
| 7.5 `PinnedRefused` declared code | §3.7, §3.8 (C6) |
| 7.5 refusal keeps its candidate | §3.8 (C7) |
| 7.5 `routing_internal_error`, `model_not_found`, D4 classified | §3.8 |
| 7.6 shaping (`drop_params`, `max_completion_tokens`, `developer`/`seed`/`logit_bias`, `include_usage`) | §3.2 |
| 7.6 SSE comments and `\r\n\r\n` | §3.4, T-D3 |
| 7.6 Google bad-key 400 → auth error | §3.7, T-D8 |
| 7.6 reasoning first-event timeout up to 120 s | §3.6, T-P1 |
| 7.6 embeddings non-SSE path | §3.9, T-D11 |
| 7.6 keyless Bedrock refused like D4 | §2.5 step 1 (`provider_unsupported` until F1c; then `credential_missing`) |
| 7.6 inactive and keyless rows omitted | §2.5 step 1 (A2) |
| 7.6 vendor quirks replayed from captures | T-D4 |
| 7.7 `pipeline_trace.routing`, no `base_url`, schema change | §3.10 |
| 7.7 `amf_upstream_failures_total{provider,code}` closed, pre-seeded | §3.10 |
| 7.7 L36-1 deltas from a single worker | §3.10, §8 |
| 7.7 `routing_decision` logs carry `request_id` | §3.10 |
| 7.8 gateway Valkey user read-only on signed state; ACLs | §3.11, T-S1 |
| 7.8 Ed25519 follow-up | §3.11, §2.11 |
| 7.9 `_FILE` secrets, `ulimits.core: 0`, `127.0.0.1` port, `depends_on` healthy, `AMF_FD_LIMIT` | §3.11 |

---
_Generated by [Claude Code](https://claude.ai/code/session_01SDCVQVj8LRKW3CZ99zfdW1)_
