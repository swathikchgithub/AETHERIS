# Aetheris

A multi-tenant, graph-driven customer issue-resolution engine. Aetheris runs
customer conversations through a stateful [LangGraph](https://github.com/langchain-ai/langgraph)
state machine — triage, hybrid RAG retrieval, LLM reasoning, dynamically
registered tool execution, response synthesis, and memory persistence — with
an explicit circuit breaker so tool-calling loops always terminate.

Administrators register tools as JSON configs (no code deploy required); they
are validated, converted into executable, schema-typed tools at runtime, and
executed through an SSRF-sandboxed HTTP layer.

## Architecture

```
triage → retrieval → reasoning ──(tool_calls?)──▶ tool_execution
                         ▲                              │
                         └──────(breaker OK)─────────────┘
                         │
                 (no tool_calls / breaker tripped)
                         ▼
                     synthesis → memory_persistence → END
```

| Layer | Responsibility | Key files |
|---|---|---|
| State schema | Validated, typed state shared by every node | [`aetheris/core/state.py`](aetheris/core/state.py) |
| Graph | Node wiring, conditional routing, circuit breaker | [`aetheris/core/graph.py`](aetheris/core/graph.py), [`routing.py`](aetheris/core/routing.py), [`nodes/`](aetheris/core/nodes/) |
| Dynamic Tool Registry | Admin JSON → validated config → executable tool | [`aetheris/tools/schema.py`](aetheris/tools/schema.py), [`registry.py`](aetheris/tools/registry.py) |
| Sandbox | SSRF-blocking HTTP executor | [`aetheris/tools/sandbox.py`](aetheris/tools/sandbox.py) |
| LLM | Claude Opus 4.8-backed reasoning/synthesis | [`aetheris/llm/anthropic_engine.py`](aetheris/llm/anthropic_engine.py) |
| API | FastAPI HTTP wrapper | [`app/main.py`](app/main.py) |
| Tests | Golden-dataset trajectory + SSRF unit tests | [`tests/`](tests/) |

See [`docs/TRD.md`](docs/TRD.md) for the full technical design and
[`docs/PRD.md`](docs/PRD.md) for product requirements. For a guided,
file-by-file explanation (interview-ready), see
[`docs/CODE_WALKTHROUGH.md`](docs/CODE_WALKTHROUGH.md).

## Requirements

- Python 3.11+
- An [Anthropic API key](https://console.anthropic.com/)

## Local setup

```bash
git clone https://github.com/swathikchgithub/AETHERIS.git
cd AETHERIS

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

pytest -v                        # 17 tests should pass, no network/API key needed

export ANTHROPIC_API_KEY=sk-ant-...
uvicorn app.main:app --reload --port 8000
```

## Try it

```bash
curl http://localhost:8000/healthz

curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Where is my order A-2002?"}'

# Continue the same conversation with the session_id from the response above
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "<session_id>", "message": "Thanks, also can I get a refund?"}'
```

## Configuring tools

Administrators define tools as JSON — see
[`config/example_tools.json`](config/example_tools.json). Each entry is
validated by [`AdminToolConfig`](aetheris/tools/schema.py) (HTTPS-only
endpoint, typed parameters, an auth scheme that references an environment
variable — never an inline secret) before it's converted into an executable
tool. Point `AETHERIS_TOOLS_CONFIG` at your own file to change the tool set.

## Deployment (Railway)

```bash
npm install -g @railway/cli
railway login
railway init          # or `railway link` for an existing project
railway variables set ANTHROPIC_API_KEY=sk-ant-...
railway up
```

Railway builds from [`Dockerfile`](Dockerfile) and health-checks
`/healthz` per [`railway.json`](railway.json). Connect the GitHub repo in
Railway's dashboard to enable auto-deploy on every push.

## Known simplifications

This is a reference implementation, not a hardened production deployment:

- **Session memory is in-process** (`InMemoryMemoryStore`) — state is lost on
  restart and won't work across multiple instances. Swap in a Postgres/Redis
  implementation of the `MemoryStore` protocol ([`aetheris/core/nodes/memory.py`](aetheris/core/nodes/memory.py)) to fix this.
- **Retrieval is a no-op** (`NullRetriever`) — no vector store is wired in.
  Implement the `Retriever` protocol ([`aetheris/core/nodes/retrieval.py`](aetheris/core/nodes/retrieval.py)) against a real backend.
- **Tools load once at startup for a single demo tenant.** A real deployment
  needs an admin API/store for per-tenant tool configs.
- **SSRF sandboxing is in-process only.** The `SandboxedHTTPExecutor`
  ([`aetheris/tools/sandbox.py`](aetheris/tools/sandbox.py)) is one layer of
  defense; production should also run tool execution in an egress-restricted
  network sandbox (gVisor/Firecracker/NetworkPolicy) as a backstop.

## License

MIT — see [`LICENSE`](LICENSE).
