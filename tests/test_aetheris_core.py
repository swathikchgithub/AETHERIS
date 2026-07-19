"""Integration tests for the Aetheris graph.

Two kinds of assertion, per the CI/CD evaluation spec:
  1. Trace-level trajectory assertions — the exact ordered list of tool
     names invoked must match the golden dataset row.
  2. Semantic validation — a pluggable judge hook (heuristic here, an
     LLM-as-judge call in production) checks the final response content.

Plus dedicated tests for the loop-ventilation circuit breaker, since a
runaway tool-calling loop is a correctness requirement, not just a nice-to-have.
"""
from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from aetheris.core.graph import build_aetheris_graph
from aetheris.core.nodes.memory import InMemoryMemoryStore
from aetheris.core.nodes.reasoning import ReasoningDecision
from aetheris.core.state import new_session_state
from tests.conftest import FakeToolRegistry, ScriptedReasoningEngine, StaticSynthesisEngine, StubRetriever
from tests.golden_dataset import GOLDEN_DATASET, GoldenRow

_TOOL_IMPLEMENTATIONS = {
    "issue_refund": lambda args: {"status": "refunded", **args},
    "get_order_status": lambda args: {"status": "shipped", **args},
}


def _semantic_judge(final_text: str, keywords: list[str]) -> bool:
    """Deterministic heuristic stand-in for an LLM-as-judge call. Kept as a
    pluggable function so this test file never needs network access; swap
    the body for a real judge-model invocation in production without
    touching the test cases themselves."""
    lowered = final_text.lower()
    return all(kw.lower() in lowered for kw in keywords)


def _run_golden_row(row: GoldenRow):
    tool_registry = FakeToolRegistry(tools=_TOOL_IMPLEMENTATIONS)
    graph = build_aetheris_graph(
        sparse_retriever=StubRetriever(),
        dense_retriever=StubRetriever(),
        reasoning_engine=ScriptedReasoningEngine(row.reasoning_script),
        synthesis_engine=StaticSynthesisEngine(),
        tool_registry=tool_registry,
        memory_store=InMemoryMemoryStore(),
    )
    initial_state = new_session_state(row.tenant_id)
    initial_state["messages"] = [HumanMessage(content=row.user_message)]
    return graph.invoke(initial_state)


@pytest.mark.parametrize("row", GOLDEN_DATASET, ids=lambda r: r.row_id)
def test_golden_dataset_tool_trajectory(row: GoldenRow):
    """Span-level trajectory assertion: the exact ordered tool names hit
    during the run must equal the golden expectation."""
    final_state = _run_golden_row(row)

    actual_trajectory = [c.tool_name for c in final_state["execution_metadata"].tool_trajectory]
    assert actual_trajectory == row.expected_tool_trajectory
    assert final_state["execution_metadata"].circuit_breaker_tripped is False


@pytest.mark.parametrize("row", GOLDEN_DATASET, ids=lambda r: r.row_id)
def test_golden_dataset_semantic_validation(row: GoldenRow):
    """Semantic validation hook over the final synthesized response."""
    final_state = _run_golden_row(row)
    final_message = final_state["messages"][-1]
    assert _semantic_judge(final_message.content, row.judge_keywords)


def test_golden_dataset_persists_to_memory_store():
    row = GOLDEN_DATASET[0]
    memory_store = InMemoryMemoryStore()
    tool_registry = FakeToolRegistry(tools=_TOOL_IMPLEMENTATIONS)
    graph = build_aetheris_graph(
        sparse_retriever=StubRetriever(),
        dense_retriever=StubRetriever(),
        reasoning_engine=ScriptedReasoningEngine(row.reasoning_script),
        synthesis_engine=StaticSynthesisEngine(),
        tool_registry=tool_registry,
        memory_store=memory_store,
    )
    initial_state = new_session_state(row.tenant_id)
    initial_state["messages"] = [HumanMessage(content=row.user_message)]

    final_state = graph.invoke(initial_state)

    persisted = memory_store.get(final_state["session_id"])
    assert persisted is not None
    assert persisted["session_id"] == final_state["session_id"]


def test_circuit_breaker_trips_on_repeated_tool_signature():
    """A reasoning engine that keeps requesting the identical tool call
    (same name, same args) must trip the breaker on the repeated-signature
    check, well before the numeric loop cap."""
    ping_pong_call = {"id": "call_x", "name": "get_order_status", "args": {"order_id": "A-9999"}}
    script = [ReasoningDecision(tool_calls=[ping_pong_call], content="") for _ in range(20)]

    tool_registry = FakeToolRegistry(tools=_TOOL_IMPLEMENTATIONS)
    graph = build_aetheris_graph(
        sparse_retriever=StubRetriever(),
        dense_retriever=StubRetriever(),
        reasoning_engine=ScriptedReasoningEngine(script),
        synthesis_engine=StaticSynthesisEngine(),
        tool_registry=tool_registry,
        memory_store=InMemoryMemoryStore(),
    )
    initial_state = new_session_state("tenant-acme")
    initial_state["messages"] = [HumanMessage(content="status please")]

    final_state = graph.invoke(initial_state, config={"recursion_limit": 100})

    meta = final_state["execution_metadata"]
    assert meta.circuit_breaker_tripped is True
    assert meta.loop_count == 2  # trips as soon as the second identical call lands


def test_circuit_breaker_trips_on_max_loops_with_varied_args():
    """Even when every call is distinct (no repeated signature), the
    numeric loop cap must still force termination within max_loops."""
    script = [
        ReasoningDecision(
            tool_calls=[
                {"id": f"call_{i}", "name": "get_order_status", "args": {"order_id": f"A-{i}"}}
            ],
            content="",
        )
        for i in range(20)
    ]

    tool_registry = FakeToolRegistry(tools=_TOOL_IMPLEMENTATIONS)
    graph = build_aetheris_graph(
        sparse_retriever=StubRetriever(),
        dense_retriever=StubRetriever(),
        reasoning_engine=ScriptedReasoningEngine(script),
        synthesis_engine=StaticSynthesisEngine(),
        tool_registry=tool_registry,
        memory_store=InMemoryMemoryStore(),
    )
    initial_state = new_session_state("tenant-acme")
    initial_state["messages"] = [HumanMessage(content="status please")]

    final_state = graph.invoke(initial_state, config={"recursion_limit": 100})

    meta = final_state["execution_metadata"]
    assert meta.circuit_breaker_tripped is True
    assert meta.loop_count == meta.max_loops
    assert "human specialist" in final_state["messages"][-1].content
