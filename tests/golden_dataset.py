"""Mock golden dataset for trace-level regression testing.

Each row pins: the input message, the expected tool-call trajectory
(ordered tool names — a span-level assertion on the agent's execution
trace), and a small set of keywords used as a deterministic stand-in for
an LLM-as-judge semantic check on the final response.
"""
from __future__ import annotations

from dataclasses import dataclass

from aetheris.core.nodes.reasoning import ReasoningDecision


@dataclass(frozen=True)
class GoldenRow:
    row_id: str
    tenant_id: str
    user_message: str
    expected_tool_trajectory: list[str]
    reasoning_script: list[ReasoningDecision]
    judge_keywords: list[str]


GOLDEN_DATASET: list[GoldenRow] = [
    GoldenRow(
        row_id="refund_happy_path",
        tenant_id="tenant-acme",
        user_message="I was charged twice for order A-1001, can I get a refund?",
        expected_tool_trajectory=["issue_refund"],
        reasoning_script=[
            ReasoningDecision(
                tool_calls=[
                    {
                        "id": "call_1",
                        "name": "issue_refund",
                        "args": {"order_id": "A-1001", "amount_cents": 1999},
                    }
                ],
                content="",
            ),
            ReasoningDecision(tool_calls=[], content="I've issued a refund for order A-1001."),
        ],
        judge_keywords=["refund", "a-1001"],
    ),
    GoldenRow(
        row_id="order_status_lookup",
        tenant_id="tenant-acme",
        user_message="Where is my order A-2002, has it shipped?",
        expected_tool_trajectory=["get_order_status"],
        reasoning_script=[
            ReasoningDecision(
                tool_calls=[
                    {"id": "call_1", "name": "get_order_status", "args": {"order_id": "A-2002"}}
                ],
                content="",
            ),
            ReasoningDecision(tool_calls=[], content="Order A-2002 has shipped and is in transit."),
        ],
        judge_keywords=["a-2002", "shipped"],
    ),
    GoldenRow(
        row_id="no_tool_needed",
        tenant_id="tenant-acme",
        user_message="What are your support hours?",
        expected_tool_trajectory=[],
        reasoning_script=[
            ReasoningDecision(tool_calls=[], content="Our support team is available 24/7."),
        ],
        judge_keywords=["support"],
    ),
]
