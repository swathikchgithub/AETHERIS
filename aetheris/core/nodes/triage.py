"""Triage/Routing node: classifies user intent from the latest message."""
from __future__ import annotations

from langchain_core.messages import HumanMessage

from aetheris.core.state import AetherisState, Intent

_KEYWORD_INTENTS: dict[str, Intent] = {
    "refund": Intent.BILLING,
    "invoice": Intent.BILLING,
    "charge": Intent.BILLING,
    "error": Intent.TECHNICAL_SUPPORT,
    "bug": Intent.TECHNICAL_SUPPORT,
    "crash": Intent.TECHNICAL_SUPPORT,
    "password": Intent.ACCOUNT_MANAGEMENT,
    "email": Intent.ACCOUNT_MANAGEMENT,
    "manager": Intent.ESCALATION,
    "escalate": Intent.ESCALATION,
}


def classify_intent(text: str) -> Intent:
    # Time: O(k) over keyword table. Space: O(1).
    lowered = text.lower()
    for keyword, intent in _KEYWORD_INTENTS.items():
        if keyword in lowered:
            return intent
    return Intent.UNKNOWN


def triage_node(state: AetherisState) -> dict:
    """Deterministic keyword classifier stands in for a lightweight
    routing-model call. Swap `classify_intent` for an LLM call without
    touching the graph or any other node (Open/Closed)."""
    last_human = next(
        (m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        None,
    )
    meta = state["execution_metadata"]
    meta.intent = classify_intent(last_human.content) if last_human else Intent.UNKNOWN
    return {"execution_metadata": meta}
