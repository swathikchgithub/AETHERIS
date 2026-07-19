"""FastAPI entrypoint for deploying the Aetheris graph as an HTTP service.

Simplification made explicit: tools are loaded once at startup for a single
demo tenant from a local JSON file, and session state lives in an in-memory
store. Swapping either for a real multi-tenant admin config store or a
Postgres/Redis-backed MemoryStore is a matter of implementing the existing
`MemoryStore` / registry-loading interfaces — the graph itself doesn't change.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from aetheris.core.graph import build_aetheris_graph
from aetheris.core.nodes.memory import InMemoryMemoryStore
from aetheris.core.nodes.retrieval import NullRetriever
from aetheris.core.state import AetherisState, new_session_state
from aetheris.llm.anthropic_engine import AnthropicReasoningEngine, AnthropicSynthesisEngine
from aetheris.tools.registry import ToolRegistry

logger = logging.getLogger("aetheris")

_TOOLS_CONFIG_PATH = os.environ.get("AETHERIS_TOOLS_CONFIG", "config/example_tools.json")
_DEMO_TENANT_ID = os.environ.get("AETHERIS_DEMO_TENANT_ID", "tenant-demo")
_TOOL_DOMAIN_ALLOWLIST = frozenset(
    filter(None, os.environ.get("AETHERIS_TOOL_DOMAIN_ALLOWLIST", "").split(","))
)

_memory_store = InMemoryMemoryStore()
_graph = None


def _build_graph():
    tool_registry = ToolRegistry()
    if os.path.exists(_TOOLS_CONFIG_PATH):
        with open(_TOOLS_CONFIG_PATH) as f:
            raw_configs = json.load(f)
        tool_registry.register_from_configs(
            raw_configs, tenant_id=_DEMO_TENANT_ID, domain_allowlist=_TOOL_DOMAIN_ALLOWLIST
        )
    else:
        logger.warning("tools config not found at %s — starting with no tools", _TOOLS_CONFIG_PATH)

    client = anthropic.Anthropic()  # resolves ANTHROPIC_API_KEY from the environment
    return build_aetheris_graph(
        sparse_retriever=NullRetriever(),
        dense_retriever=NullRetriever(),
        reasoning_engine=AnthropicReasoningEngine(client),
        synthesis_engine=AnthropicSynthesisEngine(client),
        tool_registry=tool_registry,
        memory_store=_memory_store,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _graph
    _graph = _build_graph()
    yield


app = FastAPI(title="Aetheris", lifespan=lifespan)

# No cookie-based auth is used (session_id travels in the JSON body), so a
# wildcard origin with allow_credentials=False is safe by default. Tighten
# via AETHERIS_CORS_ORIGINS (comma-separated) once you have a fixed frontend
# domain — e.g. "https://aetheris.vercel.app,http://localhost:3000".
_cors_origins_raw = os.environ.get("AETHERIS_CORS_ORIGINS", "*")
_cors_origins = ["*"] if _cors_origins_raw == "*" else [o.strip() for o in _cors_origins_raw.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class ChatRequest(BaseModel):
    tenant_id: str = Field(default=_DEMO_TENANT_ID)
    session_id: str | None = None
    message: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    tool_trajectory: list[str]
    circuit_breaker_tripped: bool


def _load_or_create_session(tenant_id: str, session_id: str | None) -> AetherisState:
    if session_id is None:
        return new_session_state(tenant_id)

    state = _memory_store.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"unknown session_id: {session_id}")

    # The circuit breaker bounds one turn's tool-calling loop, not the whole
    # session — reset per-turn bookkeeping while keeping message history.
    meta = state["execution_metadata"]
    meta.loop_count = 0
    meta.tool_trajectory = []
    meta.circuit_breaker_tripped = False
    meta.pending_tool_calls = []
    return state


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    if _graph is None:
        raise HTTPException(status_code=503, detail="graph not initialized")

    state = _load_or_create_session(req.tenant_id, req.session_id)
    state["messages"] = state["messages"] + [HumanMessage(content=req.message)]

    final_state = _graph.invoke(state)

    return ChatResponse(
        session_id=final_state["session_id"],
        reply=final_state["messages"][-1].content,
        tool_trajectory=[c.tool_name for c in final_state["execution_metadata"].tool_trajectory],
        circuit_breaker_tripped=final_state["execution_metadata"].circuit_breaker_tripped,
    )
