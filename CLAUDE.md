# CLAUDE.md

## Mission

When working in this repository, never jump directly to code changes.

Your primary responsibility is to understand the system completely before proposing fixes.

Assume that observed symptoms may not represent the actual root cause.

Assume that documentation may be outdated.

Assume that frontend state, backend state, logs, metrics, dashboards, and runtime behavior may disagree.

Always prove assumptions using evidence.

---

# Required Execution Order

Follow this order strictly.

## Phase 1: Deep Exploration

Before changing any code:

* Explore the repository structure.
* Map all major modules.
* Identify entry points.
* Identify request flows.
* Identify data flows.
* Identify state management flows.
* Identify external integrations.
* Identify configuration propagation paths.
* Identify logging and observability paths.
* Identify background processing paths.
* Identify frontend/backend interaction points.

Use aggressive parallel exploration.

Launch multiple parallel investigations covering:

### Backend Analysis

* API architecture
* Service architecture
* Routing logic
* Data layer
* Queue processing
* Background jobs
* Caching
* Configuration propagation
* Authentication
* State management

### Frontend Analysis

* Routing
* State management
* API clients
* UI workflows
* Error handling
* Loading states
* Persistence
* Browser behavior

### Infrastructure Analysis

* Deployment topology
* Runtime services
* Logs
* Metrics
* Traces
* Environment configuration

Produce a complete system understanding before making recommendations.

---

## Phase 2: Detailed Implementation Plan

After exploration:

Create a detailed implementation plan.

The plan must include:

* discovered architecture
* current behavior
* suspected issues
* confidence level
* evidence
* risks
* dependencies
* proposed fixes

Do not implement anything yet.

---

## Phase 3: Contradictory Review Agents

Before accepting the plan:

Launch 5 independent review agents.

Each agent must attempt to prove the plan wrong.

### Agent 1

Assume the current implementation is correct.

Look for false positives.

### Agent 2

Assume the implementation is fundamentally broken.

Look for hidden failures.

### Agent 3

Focus on state propagation.

Look for stale state, caching, synchronization issues.

### Agent 4

Focus on observability.

Look for mismatches between:

* logs
* traces
* metrics
* UI
* backend behavior

### Agent 5

Focus on edge cases.

Look for scenarios not covered by the plan.

Each agent must independently challenge findings.

---

## Phase 4: Devil's Advocate Review

After all review agents complete:

Perform a final Devil's Advocate review.

Challenge:

* every assumption
* every root cause
* every proposed fix

Only accept conclusions that survive contradiction and evidence review.

Evidence always wins.

Assumptions never win.

---

## Phase 5: Implementation

Only after exploration, planning, contradiction review, and Devil's Advocate review:

Implement fixes.

Prefer root-cause fixes over symptom fixes.

Avoid introducing workaround logic unless absolutely necessary.

Keep solutions simple and maintainable.

---

## Phase 6: Backend Validation

Before testing the frontend:

Perform exhaustive backend validation.

Validate:

* APIs
* services
* routing
* state transitions
* caching
* retries
* failure handling
* concurrency
* logging
* metrics
* observability

Use realistic and extreme scenarios.

Test:

* malformed inputs
* boundary conditions
* retries
* partial failures
* concurrent requests
* stale state
* race conditions
* restart scenarios
* timeout scenarios

Verify results using logs and traces.

Do not rely solely on API responses.

---

## Phase 7: Frontend Validation

After backend validation passes:

Perform strict frontend testing from a customer perspective.

Use:

* Playwright
* Browser automation
* Real browser sessions

Act as an actual customer.

Do not rely on internal assumptions.

Validate:

### Navigation

* routing
* redirects
* browser history
* refresh behavior

### State

* persistence
* reload behavior
* multiple tabs
* session expiration

### Forms

* validation
* saving
* editing
* deleting

### Workflows

* happy paths
* error paths
* recovery paths
* interrupted workflows

### UI Consistency

* desktop
* tablet
* mobile
* multiple browsers

### Frontend/Backend Consistency

Verify:

* UI state
* API responses
* database state
* logs
* metrics

All must agree.

Any mismatch is a bug.

---

## Phase 8: Extreme Edge Case Testing

Push the system beyond normal usage.

Test:

* maximum limits
* minimum limits
* repeated actions
* concurrent users
* browser refresh during operations
* network interruptions
* stale sessions
* retries
* streaming interruptions
* partial failures
* dependency failures

Continue until meaningful new failures stop appearing.

---

## Required Evidence

Every issue must include:

* title
* severity
* reproduction steps
* expected behavior
* actual behavior
* root cause
* supporting logs
* supporting traces
* screenshots if relevant
* recommended fix

Never report a bug without evidence.

Never propose a fix without understanding the root cause.

---

## Engineering Principles

1. Explore before fixing.
2. Evidence before conclusions.
3. Root cause before implementation.
4. Backend validation before frontend validation.
5. Frontend testing from a real customer perspective.
6. Contradict your own assumptions.
7. Prefer facts over intuition.
8. Verify every fix.
9. Test aggressively.
10. Do not stop after the first successful result.

The goal is not to make the issue disappear.

The goal is to understand the system, fix the correct problem, and prove the fix works under real-world conditions.
