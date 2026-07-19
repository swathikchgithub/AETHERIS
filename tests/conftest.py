"""Shared pytest fixtures and deterministic fakes for Aetheris tests.

None of these fakes touch the network — that's the point: graph tests
assert on trajectory/state, not on live HTTP or a real LLM.
"""
from __future__ import annotations

from typing import Any, Callable

import pytest
from langchain_core.messages import AIMessage

from aetheris.core.nodes.reasoning import ReasoningDecision
from aetheris.core.state import RetrievedChunk


class ScriptedReasoningEngine:
    """Deterministic fake standing in for the LLM: replays a fixed sequence
    of decisions, one per `decide()` call, so trajectory tests are
    reproducible without any network access."""

    def __init__(self, script: list[ReasoningDecision]) -> None:
        self._script = list(script)
        self._calls = 0

    def decide(self, messages, context_text, tools) -> ReasoningDecision:
        if self._calls >= len(self._script):
            return ReasoningDecision(tool_calls=[], content="Fallback: script exhausted.")
        decision = self._script[self._calls]
        self._calls += 1
        return decision


class StaticSynthesisEngine:
    """Stands in for a synthesis LLM call. Mirrors real behavior by basing
    the final response on the reasoning step's last non-empty answer (which
    already incorporates tool results via the message history) rather than
    fabricating unrelated text."""

    _FALLBACK = "Your issue has been resolved. Is there anything else I can help with?"

    def synthesize(self, messages, tripped: bool) -> str:
        for message in reversed(messages):
            if isinstance(message, AIMessage) and message.content:
                return message.content
        return self._FALLBACK


class StubRetriever:
    def __init__(self, chunks: list[RetrievedChunk] | None = None) -> None:
        self._chunks = chunks or []

    def retrieve(self, query: str, tenant_id: str, top_k: int) -> list[RetrievedChunk]:
        return self._chunks[:top_k]


class FakeTool:
    def __init__(self, fn: Callable[[dict], Any]) -> None:
        self._fn = fn

    def invoke(self, args: dict) -> Any:
        return self._fn(args)


class FakeToolRegistry:
    """Duck-typed stand-in for `ToolRegistry` (satisfies the `ToolLookup`
    protocol). Trajectory tests assert on *which* tools were called and in
    what order, not on real network I/O — that's covered separately in
    tests/test_tool_sandbox.py."""

    def __init__(self, tools: dict[str, Callable[[dict], Any]]) -> None:
        self._tools = tools

    def get(self, name: str, *, tenant_id: str):
        fn = self._tools.get(name)
        return FakeTool(fn) if fn else None

    def tool_specs(self) -> list[dict]:
        return [{"name": name, "description": name} for name in self._tools]


@pytest.fixture
def null_retriever() -> StubRetriever:
    return StubRetriever()
