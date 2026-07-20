---
active: true
iteration: 1
session_id: f42d3131-897d-4617-8420-df5c3c829576
max_iterations: 200
completion_promise: "==========================================================================
STRICT COMPLETION PROMISE (NON-NEGOTIABLE)
==========================================================================

You MUST NOT stop, summarize, return, or declare completion until the ENTIRE RAG & Vector Firewall has been PROVEN correct through live execution.

Completion is ONLY permitted when ALL of the following are true:

ARCHITECTURE

✓ Every frontend control has been verified against actual backend behavior.

✓ Every backend decision has been traced to its originating frontend configuration.

✓ Every configuration path has been validated.

✓ Every execution path has been verified.

✓ Every trust boundary has been validated.

✓ Every sequence diagram has been verified against actual execution.

PROVIDERS

✓ Every supported provider has been tested.

✓ Pinecone

✓ Milvus

✓ Chroma

✓ Custom

✓ Provider switching has been tested.

✓ Connection failures have been tested.

✓ Authentication failures have been tested.

✓ Credential rotation has been tested.

✓ BYOK has been verified.

✓ No hidden embedding provider exists.

INGESTION

✓ Every ingestion path has been tested.

✓ Single upload

✓ Bulk upload

✓ Large files

✓ Small files

✓ Updates

✓ Deletes

✓ Chunking

✓ Chunk overlap

✓ Versioning

✓ Redaction before embedding has been proven.

✓ Raw documents are never embedded before scanning.

QUERY PIPELINE

✓ Query Rewrite

✓ Query Policy

✓ Compliance

✓ Embedding

✓ Retrieval

✓ Namespace Isolation

✓ Collection Isolation

✓ Ranker

✓ Trust Score

✓ Context Assembly

✓ Generator

✓ Output Guard

Every stage has execution evidence.

ATTACK SUITE

✓ Prompt Injection

✓ Indirect Prompt Injection

✓ RAG Poisoning

✓ Embedding Poisoning

✓ Retriever Poisoning

✓ Metadata Poisoning

✓ Context Overflow

✓ Cross Tenant

✓ Cross Namespace

✓ Cross Collection

✓ PII Extraction

✓ Secret Extraction

✓ Credential Leakage

✓ IP Leakage

✓ Hallucination

✓ DoS

✓ Unicode

✓ Homoglyph

✓ Base64

✓ Zero Width

✓ Streaming

✓ Parallel Queries

✓ Concurrent Requests

Every attack has been executed.

Not reasoned about.

Executed.

CONFIGURATION

Every frontend toggle has been tested.

Enabled

Disabled

Every policy action has been tested.

Allow

Block

Redact

Rewrite

Flag

Monitor

Every precedence rule has been tested.

Every override has been tested.

Every conflict has been tested.

No hidden defaults remain.

No undocumented fallbacks remain.

No unexpected behavior remains unexplained.

VECTOR DATABASE

Every provider has been tested.

Every collection operation.

Every namespace operation.

Every retrieval operation.

Every deletion.

Every update.

Every reranker.

Every embedding model.

Every trust score.

Every chain-of-custody verification.

Every SHA verification.

Every manifest validation.

OPENAI SDK

Everything must be tested using:

✓ OpenAI SDK

✓ Raw HTTP

✓ Streaming

✓ Concurrent Requests

✓ Parallel Requests

TELEMETRY

Every dashboard counter.

Every graph.

Every latency.

Every evidence record.

Every audit entry.

Every pipeline stage.

Must correspond to actual runtime behavior.

No simulated evidence.

No stale values.

No cached values mistaken for execution.

FIXES

Every discovered issue must be:

✓ Root caused

✓ Fixed

✓ Regression tested

✓ Independently verified

Regression testing is mandatory.

If a fix breaks another component, continue working until BOTH are correct.

BLOCKED ITEMS

No item may be skipped.

If something cannot be tested:

You MUST continue investigating until ONE of these is true:

1.

It has been fixed and verified.

OR

2.

You have proven with evidence that an external dependency prevents testing.

A blocked item MUST include:

Exact reason

Root cause

Logs

Execution trace

Request

Response

Relevant code

Why no workaround exists

Blocked is NOT Skipped.

FINAL VERIFICATION

Before declaring completion:

Run one final independent verification pass over the ENTIRE RAG system.

Assume every previous fix introduced regressions.

Re-execute all critical workflows.

Re-execute all attack suites.

Re-execute all policy matrices.

Re-execute all provider tests.

Re-execute all SDK tests.

Only after every verification passes may completion be considered.

PROHIBITED

You MUST NEVER:

- Assume something works.

- Infer behavior without executing it.

- Skip edge cases.

- Skip negative tests.

- Skip failed providers.

- Skip failed attack scenarios.

- Replace testing with reasoning.

- Replace execution with assumptions.

- Ignore inconsistencies.

- Ignore unexplained behavior.

- Fabricate logs.

- Fabricate evidence.

- Fabricate successful executions.

- Claim looks"
started_at: "2026-07-11T11:44:38Z"
---

Ultrathink. Maximum effort. Think longer.

Your objective is to perform a complete end-to-end architecture validation, adversarial security assessment, debugging, hardening, and verification of the entire RAG & Vector DB Firewall subsystem.

This is NOT a feature walkthrough.

This is NOT a UI review.

This is NOT a unit test.

This is a production-grade architecture validation.

Assume every component may be incorrect until proven otherwise.

Do not stop until every stage has been verified with evidence.

==========================================================================
OBJECTIVE
==========================================================================

Prove that the RAG Firewall behaves exactly as configured from the frontend.

If a feature is disabled in the frontend,

it MUST NOT execute.

If a feature is enabled,

it MUST execute exactly as configured.

No hidden defaults.

No hidden fallbacks.

No undocumented enforcement.

No implicit behavior.

Everything must be traceable from frontend → backend → runtime → response.

==========================================================================
ARCHITECTURE VALIDATION
==========================================================================

Validate every stage.

Frontend

↓

REST API

↓

Database

↓

Configuration Loader

↓

Configuration Cache

↓

Organization Config

↓

Provider Config

↓

Collection Config

↓

Namespace Config

↓

Policy Resolution

↓

Query Processing

↓

Tier-1 Query Scan

↓

Query Rewrite

↓

Compliance Mapping

↓

Embedding

↓

Vector DB

↓

Retriever

↓

Chain of Custody

↓

Ranker

↓

Trust Scoring

↓

Generator Context Assembly

↓

Tier-1 Output Scan

↓

Tier-2

↓

Final Response

Produce sequence diagrams for every stage.

==========================================================================
VECTOR PROVIDER VALIDATION
==========================================================================

Validate every supported provider.

Pinecone

Milvus

Chroma

Custom

For every provider verify:

connection

authentication

BYOK

embedding

reranker

collection management

namespace

query

ingestion

retrieval

deletion

updates

timeouts

retries

failures

provider switching

credential rotation

invalid credentials

expired credentials

network failures

==========================================================================
BYOK VALIDATION
==========================================================================

Verify that embeddings are ALWAYS generated using the configured BYOK model.

Verify there is NEVER a hidden fallback embedding model.

Test:

valid key

invalid key

expired key

missing key

multiple providers

provider switching

reranker enabled

reranker disabled

embedding failures

Verify:

raw document

↓

scan

↓

redaction

↓

embedding

↓

storage

The embedded text must exactly match the post-scan content.

Never embed unredacted content.

==========================================================================
DOCUMENT INGESTION
==========================================================================

Test:

single document

bulk upload

large files

small files

binary files

markdown

pdf

json

yaml

xml

csv

html

code

mixed content

unicode

emoji

multi-language

very large documents

chunking

chunk overlap

deduplication

updates

deletes

versioning

Verify:

scan

policy

redaction

placeholder generation

embedding

storage

telemetry

==========================================================================
QUERY PIPELINE
==========================================================================

Validate every stage.

Query

↓

Query Rewrite

↓

Policy

↓

Compliance

↓

Embedding

↓

Retriever

↓

Namespace Isolation

↓

Collection Isolation

↓

Ranker

↓

Trust Score

↓

Generator

↓

Output Guard

↓

Response

Verify every stage independently.

==========================================================================
QUERY REWRITE
==========================================================================

Attack with:

Prompt Injection

Indirect Prompt Injection

Jailbreaks

Role Override

Ignore Previous Instructions

Prompt Smuggling

Nested Instructions

Markdown

HTML

Unicode

Homoglyph

Zero Width

Base64

Hex

XML

YAML

JSON

Tool Injection

MCP Injection

System Prompt Extraction

Verify:

rewrite

block

allow

no rewrite

false positives

false negatives

==========================================================================
VECTOR SECURITY
==========================================================================

Test:

Cross Collection Retrieval

Cross Namespace Retrieval

Cross Tenant Retrieval

Collection Enumeration

Namespace Enumeration

Unauthorized Collection Access

Unauthorized Namespace Access

Metadata Leakage

Embedding Leakage

Vector Leakage

Distance Leakage

Ranking Leakage

Similarity Leakage

Timing Leakage

Inference Leakage

Verify complete isolation.

==========================================================================
DOCUMENT POISONING
==========================================================================

Create poisoned documents.

Attempt:

Indirect Prompt Injection

Tool Poisoning

MCP Poisoning

Instruction Injection

System Prompt Override

Chain of Thought Extraction

Hidden Instructions

Markdown Injection

HTML Injection

SVG

JavaScript

JSON

YAML

Invisible Unicode

Steganography

White Text

Base64

Double Base64

Hex

Nested Payloads

Ensure:

documents are detected

quarantined

blocked

rewritten

or escalated

==========================================================================
CHAIN OF CUSTODY
==========================================================================

Validate:

SHA256

Manifest

Tampering

Hash Changes

Replay

Replacement

Document Modification

Metadata Modification

Namespace Modification

Collection Modification

Verify every document remains verifiable.

==========================================================================
POLICY VALIDATION
==========================================================================

Test every combination.

Input

Output

Both

×

Allow

Block

Redact

Rewrite

Flag

Monitor

×

Organization

Collection

Namespace

Provider

Query

Document

Verify precedence.

Verify conflicts.

Verify overrides.

Verify runtime behavior exactly matches frontend configuration.

==========================================================================
SCAN CONTROL VALIDATION
==========================================================================

If scanning is disabled:

NO scanning must execute.

If Tier-2 disabled:

NO Tier-2 execution.

If Query Rewrite disabled:

NO rewrite.

If Compliance disabled:

NO compliance.

If Document Scan disabled:

NO document scan.

If Retrieval Scan disabled:

NO retrieval scan.

If Output Scan disabled:

NO output scan.

Find any hidden defaults.

Find any undocumented fallback.

Fix them.

==========================================================================
RAG ATTACK SUITE
==========================================================================

Execute comprehensive adversarial testing.

Prompt Injection

Indirect Prompt Injection

RAG Poisoning

Data Poisoning

Document Poisoning

Retriever Poisoning

Ranking Poisoning

Embedding Poisoning

Vector Collision

Cross Tenant

Cross Namespace

Cross Collection

PII Extraction

Credential Extraction

IP Leakage

Secret Leakage

Hallucination

Grounding Bypass

Context Overflow

DoS

Long Queries

Large Documents

Streaming

Concurrent Queries

Parallel Queries

Mixed Payloads

Unicode

Homoglyph

Zero Width

Encoding

==========================================================================
GENERATOR CONTEXT
==========================================================================

Verify:

Only approved documents reach generator.

Only authorized namespaces.

Only authorized collections.

No hidden context.

No cross retrieval.

Field-level redaction.

Least privilege.

Context minimization.

Compliance propagation.

Trust propagation.

==========================================================================
OPENAI SDK
==========================================================================

Test using:

OpenAI SDK

Raw HTTP

Streaming

Concurrent Requests

Parallel Requests

Large Payloads

Malformed Requests

Multiple Models

Verify identical enforcement.

==========================================================================
PIPELINE TELEMETRY
==========================================================================

Verify telemetry reflects REAL execution.

Every counter.

Every graph.

Every latency.

Every stage.

Every evidence record.

No synthetic data.

No stale cache.

No UI-only values.

==========================================================================
ROOT CAUSE
==========================================================================

Every issue must include:

Root Cause

Code Location

Execution Path

Why It Happened

Architecture Impact

Security Impact

Performance Impact

Fix

Regression Test

Verification

==========================================================================
REGRESSION
==========================================================================

After every fix:

Re-run affected tests.

Then execute the complete RAG regression suite.

Assume every fix may introduce regressions.

==========================================================================
FINAL DELIVERABLES
==========================================================================

Produce:

Complete Architecture Report

Sequence Diagrams

Data Flow Diagrams

Trust Boundary Diagrams

Configuration Precedence Diagrams

Provider Comparison

Attack Matrix

Coverage Matrix

Regression Report

Performance Report

Security Report

Root Cause Report

Code Changes

Remaining Risks

Production Readiness Assessment

==========================================================================
STRICT COMPLETION PROMISE
==========================================================================

Do NOT stop until:

✓ Every provider has been validated.

✓ Every pipeline stage has been validated.

✓ Every attack scenario has been executed.

✓ Every frontend configuration has been verified against runtime behavior.

✓ Every policy permutation has been tested.

✓ Every scan permutation has been tested.

✓ Every namespace isolation scenario has been tested.

✓ Every collection isolation scenario has been tested.

✓ Every poisoning scenario has been tested.

✓ Every retrieval path has been tested.

✓ Every embedding path has been tested.

✓ Every reranker path has been tested.

✓ Every output path has been tested.

✓ Every discovered issue has been fixed or proven with evidence.

✓ Every fix has passed regression testing.

✓ Every conclusion is backed by logs, traces, requests, responses, code references, and live execution evidence.

Never replace execution with reasoning.

Never replace testing with assumptions.

Never fabricate evidence.

Never fabricate successful execution.

Treat every completion attempt as a hypothesis to disprove. Before returning, actively search for additional bugs, regressions, inconsistencies, hidden defaults, configuration mismatches, security vulnerabilities, and architecture flaws. Continue iterating until no reproducible issue remains and every behavior is fully explained and verified. correct.

- Claim should work.

- Claim appears fixed.

Every claim requires evidence.

COMPLETION CONDITION

The task is complete ONLY when every requirement is in exactly one state:

✓ VERIFIED

✓ FIXED & VERIFIED

✓ BLOCKED WITH COMPLETE PROOF

No other state is acceptable.

Treat every completion attempt as a hypothesis that must be disproven.

Before every attempt to finish, actively search for:

- hidden bugs
- architecture flaws
- security vulnerabilities
- configuration mismatches
- hidden defaults
- undocumented fallbacks
- false positives
- false negatives
- regressions
- missing telemetry
- incomplete coverage

Continue iterating until you can no longer produce a reproducible failure anywhere in the RAG & Vector Firewall pipeline.

Evidence—not confidence—is the only valid completion criterion.
