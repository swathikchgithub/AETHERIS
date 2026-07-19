# Code Walkthrough Script

A presenter's script for walking someone (an interviewer, a teammate)
through this codebase live. Written to be read almost verbatim, but treat it
as talking points, not a transcript — sound like yourself.

**Suggested total time:** 12–15 minutes for the full walkthrough, 5 minutes
if you only have time for the "if you only have 5 minutes" cut below.

---

## 0. Elevator pitch (30 seconds)

> "Aetheris is a multi-tenant customer-support agent built on a LangGraph
> state machine. The two things I'd point to as the interesting engineering:
> first, tool-calling loops are bounded by a circuit breaker that lives in
> the graph's routing logic, not inside the LLM call — so termination is
> structurally guaranteed, not just 'the model usually stops.' Second,
> administrators can register new backend integrations as JSON at runtime —
> no code deploy — and every one of those dynamic tool calls goes through an
> SSRF-sandboxed executor that resolves DNS itself to defeat rebinding
> attacks, not just checks the hostname string."

That's the whole pitch. Everything else is detail in service of those two
claims.

---

## 1. Architecture overview (show the diagram first)

Open [`README.md`](../README.md) or [`docs/TRD.md`](TRD.md) and point at the
topology diagram:

```
triage → retrieval → reasoning ──(tool_calls?)──▶ tool_execution
                         ▲                              │
                         └──────(breaker OK)─────────────┘
                         │
                 (no tool_calls / breaker tripped)
                         ▼
                     synthesis → memory_persistence → END
```

> "Six nodes, one loop-back edge. Triage classifies intent, retrieval pulls
> knowledge-base context, reasoning decides whether to call a tool or answer
> directly, tool_execution runs it, and then routing either sends you back
> to reasoning with the tool result or forces you to synthesis. That
> loop-back edge is the only cycle in the graph, and it's the one edge that
> matters for correctness — so that's where I put the circuit breaker."

---

## 2. State schema — [`aetheris/core/state.py`](../aetheris/core/state.py)

> "Everything flows through one `AetherisState` TypedDict — session ID,
> tenant ID, message history, retrieved context, and execution metadata.
> I used TypedDict instead of pure Pydantic because LangGraph treats state
> as per-node channel reads and writes, and TypedDict is the cheap, native
> shape for that. But `context` and `execution_metadata` are nested Pydantic
> models — so I get validation at the boundary where those objects are
> actually constructed or mutated, without paying model-validation overhead
> on every single graph hop. It's a deliberate hybrid, not an inconsistency."

Point at `ExecutionMetadata.breaker_should_trip()`:

```python
def breaker_should_trip(self) -> bool:
    return self.loop_count >= self.max_loops or self.has_repeated_signature()
```

> "This is the circuit breaker's actual logic — two independent conditions.
> A hard numeric cap, and a signature check: if the model calls the same
> tool with the same arguments twice in a row, that's a ping-pong loop and I
> trip immediately, even if we're only on iteration two of six. I'll come
> back to where this gets *enforced* in a minute — it's important that it's
> not enforced inside a node."

---

## 3. Routing — [`aetheris/core/routing.py`](../aetheris/core/routing.py)

> "This file is two small functions, and I split it out deliberately.
> `route_after_tool_execution` is the conditional edge that checks the
> breaker and decides: loop back to reasoning, or force-exit to synthesis.
> Because this is a LangGraph conditional edge — not logic buried inside the
> `reasoning` or `tool_execution` node — no particular node implementation
> can bypass it. If I swap the LLM provider tomorrow, the termination
> guarantee doesn't move with it."

---

## 4. The six nodes — [`aetheris/core/nodes/`](../aetheris/core/nodes/)

Walk through quickly — don't over-explain each one, they're short:

- **`triage.py`** — "Keyword classifier today, stands in for a cheap routing
  model call. Swappable without touching the graph — Open/Closed principle."
- **`retrieval.py`** — "Hybrid RAG: merges a sparse and a dense retriever
  with Reciprocal Rank Fusion. Both retrievers are `Protocol`s — right now
  they're `NullRetriever` stubs, but the merge logic and the interface are
  real and tested."
- **`reasoning.py`** — "Calls `ReasoningEngine.decide()` — again, a
  `Protocol` — and gets back either tool calls or a direct answer."
- **`tool_execution.py`** — "This is where `execution_metadata.register_call()`
  runs — every tool call gets appended to the trajectory and increments
  `loop_count`. This is also the node that makes the breaker's *next*
  routing decision possible."
- **`synthesis.py`** — "If the breaker tripped, this short-circuits to a
  fixed 'a human will follow up' message instead of asking the LLM to
  synthesize from a broken state — that's a deliberate fail-safe, not an
  afterthought."
- **`memory.py`** — "Repository pattern — a `MemoryStore` protocol with an
  in-memory reference implementation. Swappable for Postgres/Redis without
  touching the graph."

> "Every one of these is a factory function — `build_reasoning_node(engine,
> tools)` — that closes over its dependencies. That's dependency inversion:
> nodes depend on an interface, never a concrete class. It's *why* the test
> suite never makes a real network call or LLM request."

---

## 5. Graph assembly — [`aetheris/core/graph.py`](../aetheris/core/graph.py)

> "`build_aetheris_graph` takes every dependency as a keyword argument —
> retrievers, the reasoning engine, the tool registry, the memory store —
> and wires them into a compiled `StateGraph`. Nothing in here is hardcoded
> to Anthropic, or to any particular retrieval backend. Production calls
> this with real adapters; tests call it with fakes. Same function, same
> topology, different injected behavior."

---

## 6. Dynamic Tool Registry — the part worth the most time

### 6a. Trust boundary — [`aetheris/tools/schema.py`](../aetheris/tools/schema.py)

> "`AdminToolConfig` is the one place untrusted admin JSON becomes trusted.
> It enforces HTTPS-only endpoints, unique parameter names, and — this is
> the one I'd highlight — the auth config can only ever reference an
> *environment variable name*, never hold a literal secret. If someone tries
> to paste an API key directly into the JSON config, Pydantic validation
> rejects the auth scheme outright unless it names an env var."

### 6b. Runtime synthesis — [`aetheris/tools/registry.py`](../aetheris/tools/registry.py)

> "`ToolFactory.build()` is doing something worth pointing at:
> `pydantic.create_model()` synthesizes a brand-new Pydantic model *at
> runtime* from the admin's declared parameter list. That's how an admin's
> JSON becomes a properly typed, schema-validated LangChain tool with zero
> code written for that specific integration."

### 6c. SSRF sandbox — [`aetheris/tools/sandbox.py`](../aetheris/tools/sandbox.py) — spend real time here

> "This is the security-critical file. The naive version of SSRF protection
> checks the hostname against an allowlist and calls it done. That's
> insufficient — an attacker can register a domain that resolves to a public
> IP when you check it, and a private IP a second later when you actually
> connect. That's DNS rebinding. So `_resolve_and_validate` does the DNS
> resolution *itself*, gets the concrete IP, and checks *that* against
> blocked ranges — RFC1918, loopback, link-local, and specifically
> `169.254.169.254`, which is the cloud metadata endpoint on AWS, GCP, and
> Azure. That's the address that gets you the instance's IAM credentials if
> an SSRF filter misses it."

> "And I want to be upfront about the honest limitation here: this is an
> in-process check. The docstring says explicitly that production deployment
> should *also* run tool execution in an egress-restricted sandbox — gVisor,
> Firecracker, or a Kubernetes NetworkPolicy — so a bug in this Python code
> isn't a single point of failure. Defense in depth, not defense in one
> layer."

---

## 7. LLM integration — [`aetheris/llm/anthropic_engine.py`](../aetheris/llm/anthropic_engine.py)

> "One design point I'd call out unprompted: `AnthropicReasoningEngine.decide()`
> makes exactly *one* API call per graph visit. It is not running its own
> internal agentic loop. The graph owns the loop — the circuit breaker lives
> at the state-machine level, completely independent of whatever looping
> behavior the model provider's SDK might do internally. If I swapped Claude
> for a different provider tomorrow, the termination guarantee doesn't
> depend on that provider's tool-use loop implementation at all."

---

## 8. API layer — [`app/main.py`](../app/main.py)

> "Thin FastAPI wrapper, two routes. The one bug I actually caught while
> wiring this up: session continuation. If you resume a session and just
> re-invoke the graph with the stored state, `loop_count` from the *previous
> turn* is still sitting there — so the breaker's numeric cap would silently
> accumulate across an entire multi-turn conversation instead of bounding
> *one* turn. `_load_or_create_session` explicitly resets `loop_count`,
> `tool_trajectory`, and `circuit_breaker_tripped` on every new message while
> preserving message history. Good thing to mention if asked about testing —
> this is exactly the kind of bug that trace-level trajectory tests don't
> catch on their own, because each test only ever exercises one turn."

---

## 9. Tests — [`tests/`](../tests/)

> "Two layers. `test_aetheris_core.py` asserts the *exact ordered sequence*
> of tool calls for a golden-dataset row — trace-level assertions — using a
> `ScriptedReasoningEngine` that replays canned decisions, so there's no live
> LLM call and no network dependency in CI. There's also a dedicated pair of
> circuit-breaker tests: one proves the repeated-signature trip fires fast,
> the other proves the numeric cap fires even when every call has different
> arguments — those are two genuinely different code paths and I wanted both
> covered independently."

> "`test_tool_sandbox.py` is pure unit tests on the SSRF validator —
> loopback, RFC1918, the cloud metadata IP, non-HTTPS, off-allowlist domain,
> all rejected; one allowlisted numeric IP literal accepted, which is
> deliberately hermetic — it doesn't touch DNS, so it works with no network
> access in CI."

---

## 10. Live demo (optional, if you have a terminal up)

```bash
curl http://localhost:8000/healthz
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Where is my order A-2002?"}'
```

> "Watch the `tool_trajectory` field in the response — that's the actual
> ordered list of tools the agent called, straight from
> `execution_metadata.tool_trajectory`. Same field the tests assert on."

---

## If you only have 5 minutes

Cover, in order: the elevator pitch (§0), the diagram (§1), the circuit
breaker's two conditions in `state.py` (§2), and the SSRF/DNS-rebinding
point in `sandbox.py` (§6c). Those four are the load-bearing engineering
decisions; everything else is competent plumbing around them.

---

## Anticipated questions and how to answer them

**"Why LangGraph instead of hand-rolling the state machine?"**
> "LangGraph gives me conditional edges, a message-history reducer, and a
> compile step that validates the graph is well-formed. The parts that
> actually needed engineering — the circuit breaker, the tool sandbox, the
> dependency-inverted node design — are all mine, not the library's. I'd
> make the same call with a different graph library; the point was never to
> write a state machine from scratch."

**"Why not just trust `temperature=0` or a max-retries wrapper around the LLM call instead of a dedicated circuit breaker?"**
> "Because that only bounds *how many times you ask the model*, not *whether
> the model keeps asking for the same broken thing*. The repeated-signature
> check catches a ping-pong loop on iteration two, before you've burned four
> more calls finding out the numeric cap eventually saves you. They're
> solving different failure modes."

**"How would you make this horizontally scalable?"**
> "Two things currently assume a single process: `InMemoryMemoryStore` and
> the tool registry loaded once at startup for one tenant. Both are behind
> protocols already — `MemoryStore` and the registry's lookup interface — so
> the fix is implementing them against Postgres/Redis and a real admin
> store, not redesigning the graph."

**"What's the actual security guarantee here, and what isn't guaranteed?"**
> "The guarantee: no dynamically registered tool can reach a private,
> loopback, link-local, or cloud-metadata address, and that's proven by a
> unit test, not just asserted in a comment. What's *not* guaranteed by this
> code alone: if the Python process itself is compromised, or if there's a
> bug in `ipaddress` range matching I haven't thought of, there's no network
> namespace enforcing the same boundary. That's exactly why the docs call
> out that this needs a deployment-layer backstop."

**"What would you change if you had another week?"**
> "Real retrieval backend first — right now it's a well-tested no-op, which
> is the most visibly fake part of the system. Second, durable session
> storage, because the in-memory store is the thing that would actually
> break in a real deployment first. Third, I'd add the network-level SSRF
> backstop the code already assumes exists."
