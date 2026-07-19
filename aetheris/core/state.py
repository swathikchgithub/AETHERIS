"""Core state schema for the Aetheris agentic graph.

`AetherisState` is a LangGraph TypedDict (cheap per-node channel reads/
writes); the nested Pydantic models validate at the boundaries where state
is constructed or mutated, matching the guidance to validate at the
boundary rather than deep inside business logic.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field, field_validator

DEFAULT_MAX_TOOL_LOOPS = 6


class Intent(str, Enum):
    UNKNOWN = "unknown"
    BILLING = "billing"
    TECHNICAL_SUPPORT = "technical_support"
    ACCOUNT_MANAGEMENT = "account_management"
    ESCALATION = "escalation"


class RetrievedChunk(BaseModel):
    source_id: str
    content: str
    score: float = Field(ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UserProfile(BaseModel):
    user_id: str
    plan_tier: str = "standard"
    attributes: dict[str, Any] = Field(default_factory=dict)


class ConversationContext(BaseModel):
    """Owns retrieved/user-derived context — single source of truth for
    everything the Reasoning node needs besides raw message history."""

    user_profile: UserProfile | None = None
    retrieved_chunks: list[RetrievedChunk] = Field(default_factory=list)
    kb_query: str | None = None

    def top_context_text(self, limit: int = 5) -> str:
        # Time: O(n log n) sort of retrieved chunks. Space: O(n).
        ranked = sorted(self.retrieved_chunks, key=lambda c: c.score, reverse=True)
        return "\n---\n".join(c.content for c in ranked[:limit])


class ToolCallRecord(BaseModel):
    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    result: Any | None = None
    error: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None

    @property
    def signature(self) -> str:
        """Stable fingerprint used by the circuit breaker to detect
        tool-calling ping-pong loops (same tool, same args, repeated)."""
        return f"{self.tool_name}:{sorted(self.arguments.items())}"


class ExecutionMetadata(BaseModel):
    """Tracks *how* the graph is progressing (intent, tool trajectory, loop
    state) as distinct from conversational content, which lives in
    `messages`. This separation is what makes trace-level test assertions
    possible without parsing message content."""

    intent: Intent = Intent.UNKNOWN
    loop_count: int = 0
    max_loops: int = DEFAULT_MAX_TOOL_LOOPS
    tool_trajectory: list[ToolCallRecord] = Field(default_factory=list)
    circuit_breaker_tripped: bool = False
    pending_tool_calls: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("max_loops")
    @classmethod
    def _positive_max_loops(cls, v: int) -> int:
        if v < 1:
            raise ValueError("max_loops must be >= 1")
        return v

    def register_call(self, record: ToolCallRecord) -> None:
        self.tool_trajectory.append(record)
        self.loop_count += 1

    def has_repeated_signature(self) -> bool:
        # Time: O(n) build + O(n) set dedup. Space: O(n).
        sigs = [r.signature for r in self.tool_trajectory]
        return len(sigs) != len(set(sigs))

    def breaker_should_trip(self) -> bool:
        """Loop-ventilation circuit breaker: trips on either exceeding the
        hard iteration cap or detecting an identical tool call repeated
        (the ping-pong signature)."""
        return self.loop_count >= self.max_loops or self.has_repeated_signature()


class AetherisState(TypedDict):
    """The single LangGraph channel schema shared by every node."""

    session_id: str
    tenant_id: str
    messages: Annotated[list[BaseMessage], add_messages]
    context: ConversationContext
    execution_metadata: ExecutionMetadata


def new_session_state(tenant_id: str, *, session_id: str | None = None) -> AetherisState:
    return AetherisState(
        session_id=session_id or str(uuid.uuid4()),
        tenant_id=tenant_id,
        messages=[],
        context=ConversationContext(),
        execution_metadata=ExecutionMetadata(),
    )
