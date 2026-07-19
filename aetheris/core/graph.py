"""Assembles the Aetheris LangGraph state machine.

Topology:

    triage -> retrieval -> reasoning --(tool_calls?)--> tool_execution
                               ^                              |
                               |                              |
                               +----(breaker not tripped)-----+
                               |
                       (no tool_calls / breaker tripped)
                               v
                           synthesis -> memory_persistence -> END

The tool_execution -> reasoning edge is conditional on the circuit
breaker (see `routing.route_after_tool_execution`), which is the explicit
loop-ventilation mechanism: it guarantees the graph reaches `synthesis`
within `execution_metadata.max_loops` tool-calling iterations even if the
model keeps requesting tools.
"""
from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from aetheris.core.nodes.memory import MemoryStore, build_memory_node
from aetheris.core.nodes.reasoning import ReasoningEngine, build_reasoning_node
from aetheris.core.nodes.retrieval import Retriever, build_retrieval_node
from aetheris.core.nodes.synthesis import SynthesisEngine, build_synthesis_node
from aetheris.core.nodes.tool_execution import ToolLookup, build_tool_execution_node
from aetheris.core.nodes.triage import triage_node
from aetheris.core.routing import route_after_reasoning, route_after_tool_execution
from aetheris.core.state import AetherisState


def build_aetheris_graph(
    *,
    sparse_retriever: Retriever,
    dense_retriever: Retriever,
    reasoning_engine: ReasoningEngine,
    synthesis_engine: SynthesisEngine,
    tool_registry: ToolLookup,
    memory_store: MemoryStore,
    tool_specs: list[dict[str, Any]] | None = None,
):
    """Wires the six required nodes. `tool_registry` must additionally
    expose `tool_specs()` if `tool_specs` is not passed explicitly."""
    graph = StateGraph(AetherisState)

    resolved_tool_specs = tool_specs
    if resolved_tool_specs is None:
        tool_specs_fn = getattr(tool_registry, "tool_specs", None)
        resolved_tool_specs = tool_specs_fn() if tool_specs_fn else []

    graph.add_node("triage", triage_node)
    graph.add_node("retrieval", build_retrieval_node(sparse_retriever, dense_retriever))
    graph.add_node("reasoning", build_reasoning_node(reasoning_engine, resolved_tool_specs))
    graph.add_node("tool_execution", build_tool_execution_node(tool_registry))
    graph.add_node("synthesis", build_synthesis_node(synthesis_engine))
    graph.add_node("memory_persistence", build_memory_node(memory_store))

    graph.set_entry_point("triage")
    graph.add_edge("triage", "retrieval")
    graph.add_edge("retrieval", "reasoning")
    graph.add_conditional_edges(
        "reasoning",
        route_after_reasoning,
        {"tool_execution": "tool_execution", "synthesis": "synthesis"},
    )
    graph.add_conditional_edges(
        "tool_execution",
        route_after_tool_execution,
        {"reasoning": "reasoning", "synthesis": "synthesis"},
    )
    graph.add_edge("synthesis", "memory_persistence")
    graph.add_edge("memory_persistence", END)

    return graph.compile()
