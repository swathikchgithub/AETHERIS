# Product Requirements Document — Aetheris

| | |
|---|---|
| **Status** | Reference implementation / MVP |
| **Owner** | Swathi Kch |
| **Last updated** | 2026-07-19 |

## 1. Problem statement

Enterprise support organizations need an AI agent that can resolve customer
issues across many tenants, each with its own knowledge base, business
systems, and back-office APIs — without engineering having to hand-write and
redeploy a new integration every time a customer support team wants the
agent to call a new internal system (order lookup, refunds, account changes,
etc.).

Two failure modes make this hard to do safely:

1. **Runaway agents.** An LLM agent with tool access can get stuck alternating
   between reasoning and tool calls indefinitely, burning cost and never
   resolving the customer's issue.
2. **Unsafe dynamic integration.** Letting administrators register arbitrary
   HTTP endpoints as callable tools opens the door to Server-Side Request
   Forgery (SSRF) — an agent tricked into calling `http://169.254.169.254/`
   (cloud metadata) or an internal-only service.

Aetheris is a reference architecture that solves both: a state-machine-based
agent with a hard, provable termination guarantee, and a tool registry that
validates and sandboxes every dynamically registered integration.

## 2. Goals

- **G1 — Deterministic termination.** No conversation turn should be able to
  loop on tool calls indefinitely, regardless of what the LLM decides to do.
- **G2 — Zero-deploy tool onboarding.** An administrator should be able to
  register a new backend API as an agent tool via JSON config alone — no
  application code change, no redeploy.
- **G3 — Safe-by-default dynamic integrations.** Every dynamically registered
  tool call must be validated against SSRF before any network request is
  made, with no way for an admin misconfiguration to bypass it.
- **G4 — Multi-tenant isolation.** Tools, conversation state, and retrieved
  context must be scoped per tenant; one tenant's configuration or data must
  never be reachable from another tenant's session.
- **G5 — Verifiable correctness.** The system must support automated,
  deterministic regression testing of agent behavior (which tools fire, in
  what order) — not just "does it produce plausible text."

### Non-goals (out of scope for this version)

- A production-grade admin UI for managing tool configs (JSON file today).
- A real hybrid RAG backend (vector store + BM25) — the retrieval interface
  exists and is tested, but ships with a no-op implementation.
- Horizontally-scaled, durable session storage (currently in-process).
- Human-in-the-loop approval workflows for tool execution.
- Billing, rate limiting, or per-tenant usage quotas.

## 3. Target users

| User | Need |
|---|---|
| **Support engineering team** | Stand up a new tenant's agent quickly, wire it to that tenant's backend APIs, trust that it can't be turned into an SSRF vector. |
| **Tenant administrator** (non-engineer) | Register "give the agent the ability to check order status" without filing an engineering ticket. |
| **End customer** | Get their issue resolved in one conversation; if the agent gets stuck, get handed off to a human rather than stonewalled. |
| **Platform/security team** | Audit exactly what tools were called, with what arguments, on every conversation — and prove the sandboxing can't be bypassed. |

## 4. User stories

1. *As a support engineer*, I can add a new tool by writing a JSON entry with
   an endpoint, HTTP method, description, and parameter list — the agent can
   call it on the next session without a code deploy.
2. *As a tenant administrator*, when I register a tool, the system rejects my
   config outright if the endpoint isn't HTTPS, if it resolves to a private
   IP, or if I try to embed a secret directly instead of referencing an
   environment variable.
3. *As an end customer*, when I ask about my order, the agent looks up the
   status via the registered tool and gives me a direct answer in the same
   turn.
4. *As an end customer*, if the agent can't resolve my issue after a bounded
   number of attempts, I'm told plainly that a human will follow up — I'm
   never left in a silent infinite retry loop.
5. *As a security reviewer*, I can point the agent's tool-calling logic at
   `http://169.254.169.254/latest/meta-data/` (cloud metadata) or an internal
   RFC1918 address and see it get blocked before any request leaves the
   process — with a test proving it.
6. *As a QA engineer*, I can add a row to a golden dataset (input message +
   expected tool-call sequence) and get a regression test for it, with no
   real LLM or network call required.

## 5. Functional requirements

### FR1 — Conversational state machine
- The system must route every incoming message through: intent
  classification → context retrieval → reasoning → (optional) tool
  execution → response synthesis → persistence.
- The system must support multi-turn conversations scoped by `session_id`.

### FR2 — Circuit breaker
- The system must cap the number of tool-calling iterations within a single
  turn (`max_loops`, default 6).
- The system must detect and immediately stop on a repeated identical tool
  call (same tool, same arguments) even if under the numeric cap.
- On trip, the system must produce a clear "a human will follow up" response
  rather than an empty or truncated one.

### FR3 — Dynamic tool registry
- Tool configs are JSON documents containing: name, description, endpoint,
  HTTP method, a typed parameter list, an auth scheme, and a timeout.
- Configs must be schema-validated before use; invalid configs must be
  rejected with a clear error, not silently accepted.
- Registered tools must be presented to the reasoning model as callable
  functions with a JSON Schema matching the admin-declared parameters.
- Tools are scoped per `tenant_id`; a tool registered for one tenant must not
  be resolvable for another.

### FR4 — SSRF-safe tool execution
- Every tool call must resolve DNS and validate the resulting IP against
  private/loopback/link-local/cloud-metadata ranges before connecting.
- Only HTTPS endpoints are permitted.
- Redirects must not be followed.
- Response size must be capped.
- Per-tenant domain allowlists must be enforceable.

### FR5 — Credential handling
- Tool configs may reference credentials only by environment-variable name;
  literal secret values in a tool config must be rejected by validation.

### FR6 — Regression testing
- The system must support asserting the exact ordered sequence of tool calls
  produced for a given input ("trace-level" assertion), without requiring a
  live LLM call.
- The system must support a pluggable semantic-validation hook over the
  final response (a stand-in for LLM-as-judge).

## 6. Success metrics

| Metric | Target |
|---|---|
| Tool-calling loops that fail to terminate | 0 (structurally impossible, not just rare) |
| SSRF-blocked request attempts that reach the network | 0 |
| New tool onboarding time (admin, no engineering) | < 15 minutes |
| Golden-dataset trajectory test suite | Runs in CI with no live API calls |
| Cross-tenant tool/data leakage incidents | 0 |

## 7. Assumptions and constraints

- Anthropic's Claude API (`claude-opus-4-8`) is the reasoning/synthesis
  provider for this version; the interfaces are provider-agnostic by design.
- Deployment target for this version is a single-instance Railway container;
  the architecture assumes a future move to externalized session storage for
  horizontal scaling.
- Tool backends (order systems, refund systems, etc.) are assumed to be
  reachable over HTTPS and to authenticate via bearer token or API-key
  header.

## 8. Current status

MVP complete: state machine, circuit breaker, dynamic tool registry, SSRF
sandbox, Claude-backed reasoning/synthesis, FastAPI deployment, and a golden
regression suite are implemented and passing. See
[`docs/TRD.md`](TRD.md) for technical detail and the **Known simplifications**
section of the [README](../README.md) for what's explicitly deferred.
