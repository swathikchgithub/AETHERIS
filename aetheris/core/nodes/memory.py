"""Memory Persistence node: durably stores the session transcript and
execution trace behind a Repository interface (Dependency Inversion).
"""
from __future__ import annotations

from typing import Protocol

from aetheris.core.state import AetherisState


class MemoryStore(Protocol):
    def persist(self, state: AetherisState) -> None: ...


class InMemoryMemoryStore:
    """Reference implementation for tests/dev. Production deployments swap
    in a Postgres/Redis-backed store without touching the graph."""

    def __init__(self) -> None:
        self._sessions: dict[str, AetherisState] = {}

    def persist(self, state: AetherisState) -> None:
        self._sessions[state["session_id"]] = state

    def get(self, session_id: str) -> AetherisState | None:
        return self._sessions.get(session_id)


def build_memory_node(store: MemoryStore):
    def memory_node(state: AetherisState) -> dict:
        store.persist(state)
        return {}

    return memory_node
