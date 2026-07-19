# Technical Requirements Document — Aetheris

| | |
|---|---|
| **Status** | Implemented (reference architecture) |
| **Last updated** | 2026-07-19 |
| **Related** | [PRD](PRD.md) · [Code walkthrough](CODE_WALKTHROUGH.md) |

## 1. System overview

Aetheris is a Python service exposing a single conversational HTTP endpoint
backed by a compiled [LangGraph](https://github.com/langchain-ai/langgraph)
state machine. Each request advances one conversation turn through a fixed
topology of six nodes; state persists across turns via a pluggable memory
store.

```
                 ┌────────────┐
    HTTP ───────▶│  triage    │
                 └─────┬──────┘
                       ▼
                 ┌────────────┐
                 │ retrieval  │   (hybrid RAG — sparse + dense, RRF merge)
                 └─────┬──────┘
                       ▼
        ┌────────▶┌────────────┐
        │         │ reasoning  │◀────────────────┐
        │         └─────┬──────┘                 │
        │               │ tool_calls?             │
        │        ┌──────┴───────┐                 │
        │        ▼ yes          ▼ no              │
        │  ┌─────────────┐  ┌───────────┐         │
        │  │tool_execution│  │ synthesis │         │
        │  └──────┬──────┘  └─────┬─────┘         │
        │         │                │               │
        │  breaker tripped?        ▼               │
        │         │          ┌──────────────────┐  │
        │         no ────────┤ memory_persistence│  │
        │                    └────────┬──────────┘  │
        │                             ▼              │
        │                            END             │
        └─────────────yes────────────┘ (force to synthesis)
```

## 2. Component architecture

| Component | Module | Pattern |
|---|---|---|
| State schema | `aetheris/core/state.py` | TypedDict (graph channels) + nested Pydantic models (validated sub-objects) |
| Routing | `aetheris/core/routing.py` | Strategy (pluggable predicate functions) |
| Nodes | `aetheris/core/nodes/*.py` | Each node is a factory closing over injected dependencies (Dependency Inversion) |
| Graph assembly | `aetheris/core/graph.py` | Builder — assembles nodes + edges into a compiled `StateGraph` |
| Tool config validation | `aetheris/tools/schema.py` | Pydantic models as the trust boundary |
| Tool synthesis | `aetheris/tools/registry.py` | Factory (`ToolFactory`), Repository (`ToolRegistry`) |
| Network sandboxing | `aetheris/tools/sandbox.py` | Defense-in-depth validator + executor |
| LLM adapters | `aetheris/llm/anthropic_engine.py` | Adapter implementing the `ReasoningEngine`/`SynthesisEngine` protocols |
| HTTP API | `app/main.py` | Thin controller — no business logic |

Every node and engine dependency is expressed as a `typing.Protocol`, not a
concrete class. This is what makes the test suite possible without a live
LLM or network: `tests/conftest.py` provides fakes that satisfy the same
protocols production code depends on.

## 3. State schema

```python
class AetherisState(TypedDict):
    session_id: str
    tenant_id: str
    messages: Annotated[list[BaseMessage], add_messages]   # LangGraph message reducer
    context: ConversationContext                             # Pydantic
    execution_metadata: ExecutionMetadata                    # Pydantic
```

`ExecutionMetadata` is the trace/control object:

```python
class ExecutionMetadata(BaseModel):
    intent: Intent
    loop_count: int
    max_loops: int = 6
    tool_trajectory: list[ToolCallRecord]
    circuit_breaker_tripped: bool
    pending_tool_calls: list[dict]
```

`ToolCallRecord.signature` (`f"{tool_name}:{sorted(arguments.items())}"`) is
the fingerprint used to detect identical repeated calls — the second half of
the circuit breaker, independent of the numeric cap.

## 4. Circuit breaker — design rationale

Two independent trip conditions, evaluated by `ExecutionMetadata.breaker_should_trip()`:

1. `loop_count >= max_loops` — a hard ceiling on tool-calling iterations per
   turn, regardless of argument variation.
2. `has_repeated_signature()` — an identical `(tool_name, args)` pair called
   twice in the same turn, which trips even at `loop_count == 2`.

The breaker is enforced in `routing.route_after_tool_execution`, a
conditional edge — not inside any node — so it cannot be bypassed by a
particular reasoning implementation. Complexity: `has_repeated_signature` is
O(n) to build the signature list plus O(n) for set deduplication, where n is
the trajectory length (bounded by `max_loops`, so effectively O(1) in
practice).

## 5. Dynamic Tool Registry

### 5.1 Trust boundary

`AdminToolConfig` (Pydantic, `aetheris/tools/schema.py`) is the sole point
where untrusted admin JSON becomes trusted:

- `endpoint` must start with `https://` (field validator).
- `parameters` names must be unique.
- `AuthConfig.scheme` is one of `none | bearer_env | api_key_header_env`; a
  non-`none` scheme requires an `env_var` name — never an inline secret
  value.

### 5.2 Runtime synthesis

`ToolFactory.build(config)` (`aetheris/tools/registry.py`):

1. Synthesizes a Pydantic args model at runtime via `pydantic.create_model()`
   from the admin's declared parameters.
2. Wraps a closure that resolves auth headers, builds the request, and
   delegates execution to `SandboxedHTTPExecutor` — the tool function itself
   never calls `httpx` directly.
3. Returns a LangChain `StructuredTool`, whose `args_schema.model_json_schema()`
   is exposed via `ToolRegistry.tool_specs()` for the reasoning model's
   tool-use API.

Tools are keyed `(tenant_id, tool_name)` in `ToolRegistry`, giving hard
per-tenant isolation at lookup time (FR3/G4).

## 6. SSRF sandboxing — defense in depth

`aetheris/tools/sandbox.py` implements two layers:

**In-process validator** (`validate_url`):
1. Scheme must be `https`.
2. Hostname must be present and (if an allowlist is configured) in the
   tenant's allowlist.
3. **DNS is resolved locally** (`socket.getaddrinfo`) and the concrete IP is
   checked against blocked `ipaddress.ip_network` ranges: RFC1918
   (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), loopback, link-local
   (`169.254.0.0/16` — covers the AWS/GCP/Azure/OCI metadata endpoint
   `169.254.169.254`), CGNAT, and IPv6 equivalents.

Resolving DNS ourselves (rather than trusting the hostname string) is
required to defeat **DNS rebinding** — an attacker-controlled domain that
resolves to a public IP at validation time and a private one at connection
time.

**Executor** (`SandboxedHTTPExecutor.execute`): `follow_redirects=False`
(prevents a validated URL redirecting to a blocked one post-check), a hard
per-request timeout, and a response-size cap (`MAX_RESPONSE_BYTES`).

**Explicit non-goal of this layer:** the module docstring states this is
*one* layer of defense-in-depth. Production deployment is expected to also
run tool execution inside an egress-restricted network sandbox (gVisor,
Firecracker, or a Kubernetes NetworkPolicy scoped to the tenant allowlist) so
a bug in the in-process validator is not a single point of failure.

## 7. LLM integration

`AnthropicReasoningEngine` / `AnthropicSynthesisEngine`
(`aetheris/llm/anthropic_engine.py`) implement the `ReasoningEngine` /
`SynthesisEngine` protocols against `claude-opus-4-8` with adaptive thinking.

Key design point: **the graph owns the tool-calling loop, the LLM adapter
does not.** `AnthropicReasoningEngine.decide()` makes exactly one API call
per graph visit to the `reasoning` node; looping back to `reasoning` after a
tool executes is a graph-level conditional edge, gated by the circuit
breaker. This keeps loop termination provable at the state-machine level,
independent of any particular model provider's own agentic-loop behavior.

Message format conversion (`_to_anthropic_messages`) maps LangChain
`HumanMessage` / `AIMessage` / `ToolMessage` objects to Anthropic's
`user`/`assistant` content-block format, including `tool_use` and
`tool_result` blocks.

## 8. API contract

| Method | Path | Request | Response |
|---|---|---|---|
| `GET` | `/healthz` | — | `{"status": "ok"}` |
| `POST` | `/chat` | `{tenant_id?, session_id?, message}` | `{session_id, reply, tool_trajectory, circuit_breaker_tripped}` |

`session_id` omitted → new session. `session_id` provided → resumes stored
state (message history preserved) but **resets per-turn circuit-breaker
bookkeeping** (`loop_count`, `tool_trajectory`, `circuit_breaker_tripped`,
`pending_tool_calls`) — the breaker bounds one turn's tool-calling loop, not
the whole conversation. Unknown `session_id` → `404`.

## 9. Testing strategy

| Layer | File | What it proves |
|---|---|---|
| Trace-level integration | `tests/test_aetheris_core.py` | Exact ordered tool-call trajectory for golden-dataset rows, using `ScriptedReasoningEngine` (no live LLM, no network) |
| Circuit breaker | same file | Trips on repeated signature (fast path) and independently on the numeric cap (slow path, varied args) |
| Security unit tests | `tests/test_tool_sandbox.py` | SSRF validator rejects loopback, RFC1918, cloud metadata IP, non-HTTPS scheme, off-allowlist domain; allows an allowlisted public IP literal (no DNS dependency, hermetic) |
| Golden dataset | `tests/golden_dataset.py` | Declarative rows: input message, scripted reasoning decisions, expected tool trajectory, semantic-judge keywords |

All 17 tests run with no network access and no `ANTHROPIC_API_KEY`,
verified in CI-equivalent conditions.

## 10. Non-functional requirements

| Category | Requirement |
|---|---|
| **Termination** | Every turn must reach `synthesis` within `max_loops` tool-calling iterations — structurally guaranteed, not best-effort. |
| **Security** | No tool call may reach a private/loopback/link-local/cloud-metadata address; verified by unit test, not just code review. |
| **Isolation** | Tool lookups are keyed by `(tenant_id, name)`; no code path resolves a tool without a tenant ID. |
| **Testability** | All node dependencies are `Protocol`-typed; production and test implementations must be swappable with zero changes to graph topology. |
| **Observability** | Every tool call is recorded as a `ToolCallRecord` (arguments, result/error, timestamps) in `execution_metadata.tool_trajectory`, persisted with session state. |

## 11. Tech stack

| Concern | Choice |
|---|---|
| Orchestration | LangGraph (`StateGraph`) |
| Schema/validation | Pydantic v2 |
| LLM | Anthropic Claude API (`claude-opus-4-8`) |
| HTTP client (tool execution) | `httpx` |
| API framework | FastAPI + Uvicorn |
| Testing | `pytest` |
| Container | Docker (`python:3.12-slim`) |
| Deployment | Railway (Dockerfile builder, `/healthz` healthcheck) |

## 12. Deployment architecture

Single-container deployment (`Dockerfile` → Railway). The FastAPI
`lifespan` builds the compiled graph once at process startup — tool configs
are loaded and validated, the Anthropic client is constructed, and the
in-memory session store is initialized. See the README's **Known
simplifications** section for the explicit gaps between this and a
horizontally-scaled production deployment (durable session store, real
retrieval backend, multi-tenant admin API for tool configs).

## 13. Explicit deferred work

- Real vector/BM25-backed `Retriever` implementation.
- Durable, horizontally-shareable `MemoryStore` (Postgres/Redis).
- Multi-tenant admin API/store for tool configs (currently one static file,
  one demo tenant, loaded at process startup).
- Network-level SSRF backstop (gVisor/Firecracker/NetworkPolicy) in the
  actual deployment target — the code assumes this layer exists but does not
  provision it.
- Human-in-the-loop approval gating for tool execution.
