"""Response Synthesis node: produces the final customer-facing message."""
from __future__ import annotations

from typing import Protocol

from langchain_core.messages import AIMessage

from aetheris.core.state import AetherisState


class SynthesisEngine(Protocol):
    def synthesize(self, messages: list, tripped: bool) -> str: ...


def build_synthesis_node(engine: SynthesisEngine):
    def synthesis_node(state: AetherisState) -> dict:
        meta = state["execution_metadata"]
        if meta.circuit_breaker_tripped:
            # Fail-safe path: never let a tripped breaker reach the LLM as
            # if nothing happened — hand off explicitly instead.
            content = (
                "I've gathered what I can so far and want to loop in a "
                "human specialist to make sure this gets fully resolved."
            )
        else:
            content = engine.synthesize(state["messages"], meta.circuit_breaker_tripped)
        return {"messages": [AIMessage(content=content)]}

    return synthesis_node
