"""Hybrid RAG Retrieval node.

Combines a sparse (keyword/BM25-style) retriever and a dense (vector)
retriever behind a single `Retriever` interface so either backend can be
swapped without touching the graph (Dependency Inversion).
"""
from __future__ import annotations

from typing import Protocol

from langchain_core.messages import HumanMessage

from aetheris.core.state import AetherisState, ConversationContext, RetrievedChunk


class Retriever(Protocol):
    def retrieve(self, query: str, tenant_id: str, top_k: int) -> list[RetrievedChunk]: ...


class NullRetriever:
    """Default no-op retriever. Production deployments inject a real
    vector-store/BM25-backed implementation via `build_retrieval_node`."""

    def retrieve(self, query: str, tenant_id: str, top_k: int) -> list[RetrievedChunk]:
        return []


def _merge_hybrid(
    sparse: list[RetrievedChunk], dense: list[RetrievedChunk], top_k: int
) -> list[RetrievedChunk]:
    """Reciprocal Rank Fusion of the two ranked lists.

    Time:  O(n log n) — dominated by the final sort over merged candidates.
    Space: O(n) — one score/chunk entry per unique source_id.
    """
    scores: dict[str, float] = {}
    chunks: dict[str, RetrievedChunk] = {}
    for rank_list in (sparse, dense):
        for rank, chunk in enumerate(rank_list):
            scores[chunk.source_id] = scores.get(chunk.source_id, 0.0) + 1.0 / (60 + rank)
            chunks[chunk.source_id] = chunk
    ranked_ids = sorted(scores, key=lambda cid: scores[cid], reverse=True)[:top_k]
    return [chunks[cid] for cid in ranked_ids]


def build_retrieval_node(sparse_retriever: Retriever, dense_retriever: Retriever, top_k: int = 5):
    def retrieval_node(state: AetherisState) -> dict:
        last_human = next(
            (m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
            None,
        )
        query = last_human.content if last_human else ""
        context: ConversationContext = state["context"]
        if query:
            sparse = sparse_retriever.retrieve(query, state["tenant_id"], top_k)
            dense = dense_retriever.retrieve(query, state["tenant_id"], top_k)
            context.retrieved_chunks = _merge_hybrid(sparse, dense, top_k)
            context.kb_query = query
        return {"context": context}

    return retrieval_node
