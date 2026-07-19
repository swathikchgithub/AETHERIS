"""Routing predicates for the Aetheris state graph.

Each function inspects `AetherisState` and returns the name of the next
node. Kept separate from node bodies (Strategy pattern) so routing logic —
especially the circuit breaker — can be unit tested and swapped
independently of node implementations.
"""
from __future__ import annotations

from aetheris.core.state import AetherisState


def route_after_reasoning(state: AetherisState) -> str:
    """Dynamic Tool Execution is only entered when the reasoning step
    actually requested a tool; otherwise skip straight to synthesis."""
    meta = state["execution_metadata"]
    if meta.pending_tool_calls:
        return "tool_execution"
    return "synthesis"


def route_after_tool_execution(state: AetherisState) -> str:
    """Loop-ventilation circuit breaker: routes back to Reasoning to let
    the agent use tool results, unless the breaker has tripped (hard loop
    cap or a repeated identical tool call), in which case it force-exits
    to Synthesis so the graph always terminates."""
    meta = state["execution_metadata"]
    if meta.breaker_should_trip():
        meta.circuit_breaker_tripped = True
        return "synthesis"
    return "reasoning"
