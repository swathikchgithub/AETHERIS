"""Dynamic Tool Execution node: resolves each pending tool call against the
tenant's tool registry and executes it through the sandboxed executor.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol

from langchain_core.messages import ToolMessage

from aetheris.core.state import AetherisState, ToolCallRecord


class ToolLookup(Protocol):
    """Minimal interface this node depends on — decoupled from the concrete
    ToolRegistry so tests can inject a fake without exercising the sandbox
    or network layer (Dependency Inversion, Interface Segregation)."""

    def get(self, name: str, *, tenant_id: str) -> Any: ...


def build_tool_execution_node(registry: ToolLookup):
    def tool_execution_node(state: AetherisState) -> dict:
        meta = state["execution_metadata"]
        tool_messages: list[ToolMessage] = []

        for call in meta.pending_tool_calls:
            record = ToolCallRecord(
                call_id=call["id"],
                tool_name=call["name"],
                arguments=call.get("args", {}),
                started_at=datetime.now(timezone.utc),
            )
            tool = registry.get(call["name"], tenant_id=state["tenant_id"])
            try:
                if tool is None:
                    record.error = f"unknown tool: {call['name']}"
                else:
                    record.result = tool.invoke(call.get("args", {}))
            except Exception as exc:  # sandboxed executor already narrows failure modes
                record.error = str(exc)
            record.finished_at = datetime.now(timezone.utc)

            meta.register_call(record)
            tool_messages.append(
                ToolMessage(
                    content=str(record.error or record.result),
                    tool_call_id=record.call_id,
                )
            )

        meta.pending_tool_calls = []
        return {"messages": tool_messages, "execution_metadata": meta}

    return tool_execution_node
