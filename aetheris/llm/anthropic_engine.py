"""Anthropic-backed ReasoningEngine and SynthesisEngine implementations.

Concrete production adapters for the `ReasoningEngine` / `SynthesisEngine`
protocols the graph depends on (aetheris/core/nodes/reasoning.py,
synthesis.py) — swappable for a different provider without touching the
graph itself (Dependency Inversion).
"""
from __future__ import annotations

from typing import Any

import anthropic
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from aetheris.core.nodes.reasoning import REASONING_SYSTEM_PROMPT, ReasoningDecision

DEFAULT_MODEL = "claude-opus-4-8"
_MAX_TOKENS = 16000


def _to_anthropic_messages(messages: list) -> list[dict[str, Any]]:
    anthropic_messages: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, HumanMessage):
            anthropic_messages.append({"role": "user", "content": message.content})
        elif isinstance(message, AIMessage):
            content: list[dict[str, Any]] = []
            if message.content:
                content.append({"type": "text", "text": message.content})
            for call in message.tool_calls or []:
                content.append(
                    {"type": "tool_use", "id": call["id"], "name": call["name"], "input": call.get("args", {})}
                )
            if content:
                anthropic_messages.append({"role": "assistant", "content": content})
        elif isinstance(message, ToolMessage):
            anthropic_messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": message.tool_call_id, "content": message.content}
                    ],
                }
            )
    return anthropic_messages


def _to_anthropic_tools(tool_specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": spec["name"],
            "description": spec["description"],
            "input_schema": spec.get("input_schema") or {"type": "object", "properties": {}},
        }
        for spec in tool_specs
    ]


class AnthropicReasoningEngine:
    """One Claude call per graph visit. The graph — not this class — owns
    the tool-calling loop and circuit breaker, so `decide()` is a single
    decision, not an agentic loop."""

    def __init__(self, client: anthropic.Anthropic, model: str = DEFAULT_MODEL) -> None:
        self._client = client
        self._model = model

    def decide(self, messages: list, context_text: str, tools: list[dict[str, Any]]) -> ReasoningDecision:
        system = REASONING_SYSTEM_PROMPT
        if context_text:
            system = f"{system}\n\n<retrieved_context>\n{context_text}\n</retrieved_context>"

        response = self._client.messages.create(
            model=self._model,
            max_tokens=_MAX_TOKENS,
            thinking={"type": "adaptive"},
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=_to_anthropic_tools(tools),
            messages=_to_anthropic_messages(messages),
        )

        tool_calls = [
            {"id": block.id, "name": block.name, "args": block.input}
            for block in response.content
            if block.type == "tool_use"
        ]
        text = "".join(block.text for block in response.content if block.type == "text")
        return ReasoningDecision(tool_calls=tool_calls, content=text)


class AnthropicSynthesisEngine:
    """Composes the final customer-facing reply from the full turn history,
    including any tool results already appended to `messages`."""

    _SYNTHESIS_INSTRUCTION = (
        "Based on the conversation and any tool results above, write the final "
        "customer-facing reply. Be direct and concise. Do not mention internal "
        "tools, systems, or reasoning."
    )

    def __init__(self, client: anthropic.Anthropic, model: str = DEFAULT_MODEL) -> None:
        self._client = client
        self._model = model

    def synthesize(self, messages: list, tripped: bool) -> str:
        anthropic_messages = _to_anthropic_messages(messages)
        anthropic_messages.append({"role": "user", "content": self._SYNTHESIS_INSTRUCTION})

        response = self._client.messages.create(
            model=self._model,
            max_tokens=_MAX_TOKENS,
            system=REASONING_SYSTEM_PROMPT,
            messages=anthropic_messages,
        )
        return "".join(block.text for block in response.content if block.type == "text")
