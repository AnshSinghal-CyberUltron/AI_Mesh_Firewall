# OpenAI Vector Stores API (retrieval-filesearch)

## Current
ZeroShield currently exposes retrieval through 4 existing gateway surfaces:

1. **POST /v1/rag/query** (main.py:7758) — Query vector collections with firewall enforcement. Accepts query_text, collection_name, n_results, where_filter. Returns documents array + x-zeroshield metadata. Routes through RAGFirewallPipeline (4 stages: query, retriever, ranker, generator) with policy enforcement via RAG_PIPELINE.execute().

2. **POST /v1/rag/ingest** (main.py:8343) — Ingest documents into collections. Accepts documents array (text, id, metadata), collection_name, vector_db_type. Enforces batch/char caps (RAG_INGEST_MAX_DOCS=1000, RAG_INGEST_MAX_CHARS=2M). Routes through firewall with ContextGuard scanning, PII redaction, embedding generation, upsert to vector DB.

3. **POST /v1/vector/* family** (vector_routes.py:1-1112) — Portable multi-tenant RAG interface. Four endpoints: POST /query (L366-629), POST /upsert (L636-885), POST /delete (L892-1033), GET /config (L1040-1111). Org-namespaced via Bearer token auth. Resolves vector provider per-org from Redis (vector:provider:{org_id}:*). Enforces collection-name isolation via __-separator guard (M-10, L182-193). Returns x-zeroshield metadata. No firewall enrichment on /vector/* endpoints (different codepath from /rag/*).

4. **GET /v1/models** (main.py:10411) — Lists available LLM models. Returns OpenAI-compatible model list. No file metadata surfaces.

**Control plane:**
- /api/vector-policies/ (CRUD + compile) — VectorCollectionPolicy model (policy/vector_models.py L33-150+) defines namespace, RBAC, embedding anomaly thresholds, content-filter rules.
- /api/vector-providers/ — VectorProviderConfig (org-level BYOK provider routing: Pinecone, Milvus, Chroma, Custom).

**Data model:** VectorCollectionPolicy(organization, project_id, collection_name, vector_db_type, namespace, allowed_operations, max_results_per_query, require_context_scan, embedding_model, embedding_dimension). No file/metadata tracking. No vector_store IDs, file IDs, batch processing IDs.

## OpenAI Spec
OpenAI Retrieval-Filesearch API shape (from Assistants + Responses API patterns):

1. **Vector Stores API** (POST /v1/vector_stores, GET /v1/vector_stores/{vs_id}, DELETE /v1/vector_stores/{vs_id})
   - Create: {name, expires_after?:{anchor, days}, metadata?}
   - Response: {id: "vs_*", object: "vector_store", created_at, name, status: "processing"|"completed", file_count, usage_bytes, ...}

2. **Vector Store Files API** (POST /v1/vector_stores/{vs_id}/files, GET /v1/vector_stores/{vs_id}/files, GET /v1/vector_stores/{vs_id}/files/{file_id}, DELETE /v1/vector_stores/{vs_id}/files/{file_id})
   - Upload file: multipart/form-data, file + metadata
   - Response: {id: "file-*", object: "vector_store.file", created_at, vector_store_id, status: "in_progress"|"completed"|"failed", ...}

3. **Files API** (POST /v1/files, GET /v1/files/{file_id}, DELETE /v1/files/{file_id})
   - Upload (multipart): file, purpose: "assistants" | "vision" | "batch"
   - Response: {id: "file-*", object: "file", bytes, created_at, filename, purpose, status}

4. **File Search Tool** (within /v1/chat/completions + Responses API)
   - tools: [{type: "file_search", file_search: {max_results: 20}}]
   - tool_choice: "required" | "auto" | {type: "file_search"}
   - Tool use in completion: tool_call.id, function.name="file_search", arguments={query, vector_store_id}
   - Response: tool output with matched file chunks (ID, score, text)

5. **Responses API** (POST /v1/responses, *new* — typed input/output items + tools)
   - Input items: {type: "text", text: "..."}, {type: "input_file", file: file_id}, {type: "document", document: {type: "text"|"pdf", text|source}}
   - Output items streamed: {type: "message", message: {...}}, {type: "output_file", file: {...}}, {type: "output_file_citation", file_id, chunk_text}
   - Tools: function_calling, file_search (max_results), web_search
   - Streaming events: response.created, response.output_text.delta, response.output_file_citation.delta, response.completed
   - State: previous_response_id for continuations

6. **Usage API** (GET /v1/organization/usage/vector_stores) — track vector store usage by org

## Reusable hooks
**Existing firewall components to inherit enforcement (no duplication):**

1. **ContextGuard (input scanner)** — rag_pipeline/pipeline.py:78-79, already used in query_stage.py. Reuse for file content scanning: ContextGuard.scan(content) before embedding. No new code needed — plug file bytes into existing scan_text path.

2. **RAGFirewallPipeline.execute()** — rag_pipeline/pipeline.py:87-146. All file-search queries already funnel through this 4-stage pipeline (query, retriever, ranker, generator). Reuse for file_search tool results: pass {query, vector_store_id as collection_name, ...} → same pipeline applies all policies.

3. **pipeline_trace.py + output_guard.py** — already integrated into pipeline. File citation output (which documents LLM cited) flows through output_guard redaction. No new trace format needed if citations reuse document manifests (DocumentManifest in contracts.py L22-29).

4. **VECTOR_POLICY_SYNC** — rag_pipeline/pipeline.py:85, hot-updates compiled policies per org (L111-114). File-search policies (max_results, allowed_operations: ['query']) inherit via existing VectorCollectionPolicy. No new policy model.

5. **Rate limiter + Circuit breaker** — already in retriever_stage.py. File search tool-use calls also hit rate-limiter (same as manual queries). Tool-use loop depth capped in main pipeline (no new component).

6. **Telemetry.emit()** — vector_routes.py:840-850, main.py telemetry integration. Reuse for file events: TELEMETRY.emit({event_type: 'vector_file_upload', org_id, file_id, status, ...}). No new telemetry spec.

7. **Org-scoped vector client resolution** — vector_routes.py:316-358 (_resolve_vector_provider_for_org). File operations (upload, delete, query) already use this per-org routing. Reuse unchanged.

8. **Auth + Redis token resolution** — vector_routes.py:238-313 (_resolve_org_from_token). Reuse for /v1/vector_stores/* and /v1/files/* endpoints. No changes needed.

9. **x-zeroshield metadata + response contract** — main.py:_build_zeroshield_metadata + ZeroShieldResponse.v1.md. File operations (upload, delete) return standard x-zeroshield {action, request_id, ...} already compatible. Reuse unchanged.

10. **Streaming SSE + trace frames** — stream_orchestration.py:build_stream_trace_frame (M-51 contract). File-search tool-use results and output file citation events flow through same stream finalize choke point (stream_with_finalize L420). One-frame-per-termination invariant already enforced.

**Enforcement inheritance summary:** No firewall duplication needed. All new /v1/vector_stores/* and /v1/files/* endpoints use existing ContextGuard (input), RAGFirewallPipeline.execute (query), VECTOR_POLICY_SYNC (policy), rate-limiter + circuit-breaker (DoS). Only new code: data models (VectorStore, File, VectorStoreFile) + CRUD endpoint handlers (passthrough to pipeline, auth gate via _resolve_org_from_token).

## Gaps
[
  {
    "title": "Vector Store CRUD (POST /v1/vector_stores + GET/DELETE variants)",
    "severity": "Critical",
    "detail": "OpenAI exposes named, versioned vector stores with per-store lifecycle (create, list, get metadata, delete, expire after N days). ZeroShield has only implicit collection-based retrieval (query via collection_name string). Gaps: (1) No persistent vector_store object with ID, metadata, file_count tracking; (2) No list/get/delete endpoints for store lifecycle; (3) No expiration/TTL control; (4) Collection name collision risk across orgs (mitigated partially by org-ns but no explicit store ownership model); (5) No way to discover available stores per org/user.",
    "files": "gateway/vector_routes.py:316-358 (missing store resolution), control/policy/vector_models.py (no VectorStore model, only VectorCollectionPolicy)",
    "implementationApproach": "Add VectorStore model to control plane (Django): {id: UUID, org_id, name, status (processing/completed), created_at, expires_after_days, file_count, usage_bytes, metadata_json}. Gateway: (1) POST /v1/vector_stores -> create VectorStore record, sync to Redis (vector:store:{store_id}). (2) GET /v1/vector_stores -> list org stores from Redis/DB. (3) GET /v1/vector_stores/{store_id} -> fetch metadata (org-scoped). (4) DELETE /v1/vector_stores/{store_id} -> mark deleted, cascade file deletions. (5) PATCH to update metadata/TTL. Reuse org-resolution from vector_routes.py:238-313 (_resolve_org_from_token). Store ID becomes the new collection-name namespace (escape __-guard by prefixing store_id). Emit telemetry on lifecycle changes (TELEMETRY.emit).",
    "effort": "M",
    "firewallRisk": "Vector store lifecycle is an ingest choke point \u2014 store creation/deletion should enforce policy (creation allowed per org? deletion requires owner?). Mitigation: (1) Gate POST /v1/vector_stores behind VectorCollectionPolicy.allowed_operations check (future: 'create_store'). (2) SSRF guard on store metadata URLs (if future store configs support external blob URIs). (3) Audit log all store deletions (org_id, actor, store_id, file_count)."
  },
  {
    "title": "File Upload + Multipart Handling (POST /v1/vector_stores/{vs_id}/files)",
    "severity": "Critical",
    "detail": "OpenAI accepts multipart/form-data file uploads with optional metadata, scans files, chunks them, and indexes into a vector store. ZeroShield currently handles JSON-only document ingest (POST /v1/rag/ingest accepts documents array with text strings). Gaps: (1) No multipart/form-data parser; (2) No file metadata tracking (filename, mime type, size); (3) No file object identity (OpenAI returns file_id); (4) No async chunking/embedding pipeline (current ingest is inline); (5) No file-scoped deletion or retrieval; (6) No file status lifecycle (in_progress/completed/failed).",
    "files": "gateway/main.py:8343-8437 (rag_ingest accepts JSON only), gateway/vector_routes.py:642-873 (upsert accepts documents array, no file upload), gateway/rag_collections.py (no FileModel)",
    "implementationApproach": "Add File + VectorStoreFile models to control plane: File(id, org_id, filename, purpose, bytes, mime_type, created_at, status), VectorStoreFile(id, vector_store_id, file_id, status, usage_bytes, chunk_count). Gateway: (1) POST /v1/vector_stores/{vs_id}/files \u2014 FastAPI UploadFile multipart handler. Enforce size cap (FILE_MAX_BYTES=10M from env). (2) Scan file content via existing ContextGuard (injection, PII, toxicity \u2014 pipeline_trace.py integration). (3) If Tier-2 scan available, call guard-model (future: async Celery task). (4) On pass: delegate to RAG ingest pipeline (parse PDF/DOCX/TXT, chunk, embed). (5) Return {id: 'file-*', status: 'in_progress'|'completed', created_at}. (6) GET /v1/vector_stores/{vs_id}/files \u2014 list files for store (org-scoped). (7) DELETE /v1/vector_stores/{vs_id}/files/{file_id} \u2014 mark deleted, trigger cascade cleanup. Reuse ContextGuard from RAG pipeline (already in pipeline.py:79); chain file scanning into query_stage before embedding dispatch.",
    "effort": "L",
    "firewallRisk": "Files are a new input surface \u2014 CRITICAL enforcement points: (1) File content scanning is MANDATORY before embedding (ContextGuard.scan inline or via Celery). (2) Mime-type validation (block executables, archives unless whitelisted). (3) File size DoS: enforce FILE_MAX_BYTES globally + per-org daily quota (future: rate-limiter integration). (4) Chunk extraction injection: chunking library (e.g., llama-index, langchain) may parse malicious PDFs \u2192 embedded prompt injections. Mitigation: scan chunks post-extraction before embedding. (5) File metadata leakage: don't expose file content/chunk text in list APIs, only metadata. Sanitize error messages (don't leak 'PDF parse failed: ...')."
  },
  {
    "title": "Typed Input Items (Responses API document ingest)",
    "severity": "High",
    "detail": "Responses API (OpenAI's new inference-time API) accepts typed input items: {type: 'text'|'input_file'|'document'} where 'document' can be {type: 'text'|'pdf', text|source}. This allows inline document submission (no prior file upload) for RAG in a single request. ZeroShield /v1/chat/completions only accepts messages (role/content strings). Gaps: (1) No /v1/responses endpoint; (2) No typed input parsing (text vs file vs inline document); (3) No document type negotiation (plaintext vs PDF bytes); (4) No inline document scanning before retrieval.",
    "files": "gateway/main.py:3484-3650 (chat_completions handler, messages-only), gateway/rag_pipeline/contracts.py (no InputItem typedef)",
    "implementationApproach": "New endpoint: POST /v1/responses (distinct from /v1/chat/completions, reuses firewall pipeline). (1) Parse body: {model, instructions?, tools?, input: [{type, text|file|document}, ...]}. (2) Validate input types: 'text' -> string, 'input_file' -> file_id (lookup in Files API), 'document' -> {type, text|source (URL)}. (3) For 'document' type: fetch/parse content (inline text or fetch from URL, scan via ContextGuard). (4) Build unified retrieval context: combine text inputs + document text + RAG results (if file_search tool enabled). (5) Route to LLM via LLM_ROUTER (shared with /v1/chat/completions). (6) Emit output items (typed): {type: 'message'|'output_file_citation', ...} in response stream. Gate responses endpoint behind CONFIG flag (ENABLE_RESPONSES_API=false default). Enforce: typed input parsing validation (reject unknown types early as 400). Store input document URLs in audit trail (future compliance audit).",
    "effort": "L",
    "firewallRisk": "Typed inputs introduce URL-fetching surface (source URLs in document items): SSRF risk. Mitigations: (1) Validate source URLs (block RFC1918, metadata IPs \u2014 reuse _url_guard from rag_collections.py:42-48). (2) Timeout fetch (5s max). (3) Limit fetch size (10 MB, same as file size cap). (4) Log all fetches with URL + org_id. (5) Scan fetched content same as uploaded files (ContextGuard + optional Tier-2). (6) Do NOT inline document source URLs in audit logs/responses (strip protocol, store only domain hash)."
  },
  {
    "title": "File Search Tool Definition + Streaming (tools: [{type: 'file_search', file_search: {max_results}}])",
    "severity": "High",
    "detail": "OpenAI chat/responses API accepts tools array with file_search type. When enabled, LLM can call file_search tool during generation (tool_use). ZeroShield /v1/chat/completions accepts tools parameter (llm_router.py:65 includes tool_choice, but no explicit file_search handling). Gaps: (1) No parsing of tools[].type == 'file_search'; (2) No tool-use streaming (tool calls are not traced to file_search); (3) No file_search result injection back into LLM context; (4) No max_results enforcement per file_search config; (5) No streaming output_file_citation events.",
    "files": "gateway/main.py:3630 (tools param forwarded raw), gateway/llm_router.py:65 (tool_choice listed but no file_search parsing), gateway/stream_orchestration.py (no file_search result streaming)",
    "implementationApproach": "(1) Parse tools array in chat_completions handler (main.py prolog, ~L3630). Extract file_search objects: {type: 'file_search', file_search: {max_results, vector_stores: [store_id]}}. (2) Validate: max_results in [1, 100], vector_stores list not empty (if specified). (3) Inject file_search metadata into request context (thread into CONTEXT). (4) On tool-use streaming: when LLM emits tool_call with function.name='file_search', intercept in stream_orchestration.py (secure_streaming.py:SecureStreamingResponse._flush_buffer or stream_orchestration.py:build_stream_trace_frame). (5) Execute file search via RAG query (reuse RAG_PIPELINE.execute, pass vector_store_id as collection_name). (6) Marshal results as tool output (tool_call_id, type: 'tool_result', content: [{type: 'document', source: file_id, text: chunk}]). (7) Return to LLM as tool result in next turn (not end-user facing \u2014 LLM-internal context). (8) Stream output_file_citation events (optional, for visibility). Gate behind tool_use feature flag.",
    "effort": "M",
    "firewallRisk": "Tool-use is a form of agent agentic behavior \u2014 new enforcement surface: (1) Max tokens/turns for agentic loops (already have circuit-breaker for vector queries, but tool-use loop has no depth limit \u2192 DoS on many tool calls). Mitigation: cap tool-use turns (AGENTIC_MAX_TURNS=10 env var, same as conversation depth). (2) Tool filtering: restrict which tools a user/org can invoke (future VectorCollectionPolicy.allowed_tools? For now: no org control, all orgs get file_search if enabled). (3) Injection via tool results: file_search returns LLM-visible content \u2192 LLM can be tricked by malicious documents to act on them. Mitigation: file_search results already scanned (ranker_stage.py sanitizes), but revalidate on tool-result marshal (no raw doc text, only summary)."
  },
  {
    "title": "Streaming Output File Citation Events (response.output_file_citation.delta)",
    "severity": "Medium",
    "detail": "Responses API streams output items including file citations: {type: 'output_file_citation', index, file_id, chunk_text}. Allows clients to see which documents the LLM cited. ZeroShield streams RAG context bindings (context_binding_id) but not per-chunk file citations. Gaps: (1) No streaming event type for citations; (2) No file_id attribution in streamed chunks; (3) No chunk-text sampling (what the LLM saw) in stream output.",
    "files": "gateway/stream_orchestration.py:348-420 (build_stream_trace_frame, no citation events), gateway/rag_pipeline/generator_stage.py (context binding but no per-chunk file tracking)",
    "implementationApproach": "(1) Extend RetrieverStageOutput.documents to include source_file_id field (if document came from file upload vs inline ingest). (2) In RankerStageOutput, preserve file_id through document ranking. (3) In streaming path (stream_orchestration.py:stream_with_finalize), before yielding each LLM chunk, emit optional output_file_citation event when streamed text cites a document: {type: 'output_file_citation', file_id, chunk_text: <snippet from retrieved doc>}. (4) Gate behind config flag (STREAM_CITATION_EVENTS=false default). (5) Sanitize chunk_text (redact PII if output_guard already flagged it). Optional \u2014 file citations are lower-priority than core file_search tool support.",
    "effort": "S",
    "firewallRisk": "Minimal \u2014 chunk_text in citation events is already filtered (output_guard has already redacted PII/secrets in the document at ranker stage). Risk: over-sampling (streaming too many citation events exhausts bandwidth). Mitigation: emit citation only once per unique file_id per request, not per chunk."
  },
  {
    "title": "Vector Store File Deletion Cascade + Quota Enforcement",
    "severity": "High",
    "detail": "When a vector store is deleted, all its files should be cascade-deleted. When files are uploaded, org quota (storage bytes, file count) should be enforced. OpenAI exposes quota via /v1/organization/usage/vector_stores. ZeroShield has no quota model or cascade logic. Gaps: (1) No file_count tracking per vector store; (2) No usage_bytes tracking; (3) No org-level quota (max storage, max files); (4) No cascade delete when vector store deleted; (5) No quota exceeded error (413 Content Too Large with quota info).",
    "files": "gateway/vector_routes.py:976-1020 (delete endpoint, no cascade), control/policy/vector_models.py (no quota fields)",
    "implementationApproach": "(1) Add to VectorStore model: file_count (int), usage_bytes (int, auto-sum). Add to Organization model (auth_api): vector_store_quota_files (default 1000), vector_store_quota_bytes (default 100 GB). (2) On file upload (POST /v1/vector_stores/{vs_id}/files): before commit, check org quota: if usage_bytes + new_file_size > quota_bytes OR file_count + 1 > quota_files, return 413 {error: 'quota_exceeded', usage: {...}, limit: {...}}. (3) On file delete: decrement VectorStore.file_count + usage_bytes. (4) On vector store delete: cascade delete all files (VectorStoreFile.delete()), trigger vector client deletion (vector_client.delete_collection). (5) Track quota usage in telemetry (TELEMETRY.emit {event: 'vector_quota_check', org_id, used_bytes, limit_bytes, ...}).",
    "effort": "M",
    "firewallRisk": "Quota is a DoS defense mechanism \u2014 critical to prevent org from exhausting shared gateway storage. Mitigations: (1) Quota checks must be FAST (Redis-backed, not DB query per upload). Store quota in Redis: 'vector:quota:{org_id}:{bytes|files}' updated on every file op. (2) Enforce quota BEFORE file ingestion (scan + embed + store), not after \u2014 fail fast on quota check. (3) Log all quota-exceeded events with org_id + actor (potential abuse detection)."
  },
  {
    "title": "Batch Upload API (POST /v1/vector_stores/{vs_id}/file_batches)",
    "severity": "Medium",
    "detail": "OpenAI offers optional batch file upload (upload multiple files, receive batch_id, poll status). ZeroShield /v1/rag/ingest handles batch documents inline but no file-level batching. Gaps: (1) No batch_id tracking; (2) No async batch processing (ingest is synchronous, blocking); (3) No batch status polling (in_progress \u2192 completed); (4) No partial-failure handling (3/5 files succeeded).",
    "files": "gateway/main.py:8343-8437 (rag_ingest, inline only), gateway/vector_routes.py:642-873 (upsert, inline only)",
    "implementationApproach": "Optional feature (skip if not on roadmap). If implementing: (1) Add FileBatch model (id, vector_store_id, status: 'in_progress'|'completed'|'failed', file_count, succeeded_count, error_log). (2) POST /v1/vector_stores/{vs_id}/file_batches \u2192 create FileBatch, queue Celery task (async ingest). (3) Return {batch_id, status: 'in_progress'}. (4) GET /v1/vector_stores/{vs_id}/file_batches/{batch_id} \u2192 poll status + succeeded_count + errors. (5) Reuse existing /v1/rag/ingest logic in Celery worker (chunk, embed, scan, store). Low priority \u2014 first ship single-file upload.",
    "effort": "M",
    "firewallRisk": "Async processing introduces audit trail gaps: if file scan fails in Celery worker, audit entry may not reach logging pipeline before task retry. Mitigations: (1) Emit scan result events to Kafka/pubsub (decoupled from task lifecycle). (2) Store batch audit in DB (FileBatch.scan_verdict_json) before queuing worker. (3) On task failure, emit DLQ event with org_id + batch_id (future: SOC dashboard alerts)."
  },
  {
    "title": "File Expiration + Soft-Delete (expires_after, status: 'deleted')",
    "severity": "Medium",
    "detail": "OpenAI Files API supports expires_after (TTL). ZeroShield has no TTL model. Gaps: (1) No expires_at timestamp on files; (2) No background job to purge expired files; (3) No soft-delete status (allows recovery).",
    "files": "gateway/rag_collections.py (no TTL logic), control/policy/vector_models.py (no File model)",
    "implementationApproach": "(1) Add expires_at (optional DateTimeField) to File model. (2) Add expires_at to VectorStore model (vector_store expiration). (3) Background job (Celery beat, daily): find files with expires_at < now(), set status='deleted', trigger cascade cleanup (vector_client.delete_vectors for that file). (4) GET /v1/files/{file_id} returns error 410 (gone) if status='deleted'. (5) Add HARD_DELETE_AFTER_DAYS env var (default 30) \u2014 after N days in 'deleted' status, purge from storage (vector DB + S3 if archived). Low priority.",
    "effort": "S",
    "firewallRisk": "Minimal \u2014 soft-delete allows audit trail recovery (logs still reference file_id even after deletion). Hard delete after grace period is a GDPR feature."
  },
  {
    "title": "File Metadata Filtering in List/Query APIs",
    "severity": "Medium",
    "detail": "OpenAI /v1/vector_stores/{vs_id}/files supports filtering by status. ZeroShield /v1/rag/documents and /v1/vector/query have limited metadata filtering (only where_filter on document metadata, no file-level filtering). Gaps: (1) No filter by file_id in query results; (2) No filter by status in list files; (3) No search within file metadata (filename, mime_type).",
    "files": "gateway/main.py:9012-9160 (rag_documents, basic document list), gateway/vector_routes.py:366-629 (query accepts where_filter but not file-level filter)",
    "implementationApproach": "(1) Extend /v1/vector_stores/{vs_id}/files?status=in_progress to support query params. (2) GET /v1/vector_stores/{vs_id}/files?status=completed&limit=20&order=-created_at. (3) Extend /v1/rag/query body to accept optional file_ids filter: {query, file_ids: ['file-*'], ...}. (4) In retriever_stage.py, pass file_ids to vector client query (filter results to only those from specified files). (5) Return file_id in each document result (already tracked if source_file_id added in Gap 5).",
    "effort": "S",
    "firewallRisk": "Minimal \u2014 filtering is read-only (no new auth bypass). Risk: filter injection in file_ids array (attacker tries to access other org's files). Mitigation: validate each file_id is owned by the requesting org (check VectorStoreFile.vector_store_id matches org_id)."
  },
  {
    "title": "Organization Usage API (GET /v1/organization/usage/vector_stores)",
    "severity": "Low",
    "detail": "OpenAI exposes /v1/organization/usage/vector_stores with aggregate stats (total_vector_stores, total_files, total_bytes, period). ZeroShield has no usage endpoint. Gaps: (1) No org-level aggregation; (2) No usage billing integration; (3) No per-period tracking.",
    "files": "No existing endpoint",
    "implementationApproach": "(1) GET /v1/organization/usage/vector_stores (org-scoped, requires Bearer key). (2) Aggregate from DB: VectorStore.objects.filter(org_id=auth_org_id).count(), sum(usage_bytes), sum(file_count). (3) Return {vector_stores: count, files: count, bytes: usage_bytes, period: {start_date, end_date}}. (4) Cache in Redis (1h TTL) to avoid DB query per request. Low priority \u2014 not blocking core file_search.",
    "effort": "S",
    "firewallRisk": "None \u2014 read-only aggregation, org-scoped."
  }
]