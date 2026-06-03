"""Retrieval observability / tracing (audit fix #9).

Persists a structured trace of every retrieval call so failures can be
diagnosed offline:

    {
      query, search_invoked, generated_queries, retrieved_chunks,
      reranked_chunks, chunks_sent_to_llm, final_answer, latency_ms, ...
    }

Traces are written as JSON Lines to ``RETRIEVAL_TRACE_DIR`` (one file per UTC
day, ``retrieval-YYYY-MM-DD.jsonl``). Writing is feature-flagged via
``ENABLE_RETRIEVAL_TRACING`` and fully best-effort: any failure is swallowed so
tracing can never break a user request. Files older than
``RETRIEVAL_TRACE_RETENTION_DAYS`` are pruned opportunistically.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from typing import Any

from docubrain.configs.chat_configs import ENABLE_RETRIEVAL_TRACING
from docubrain.configs.chat_configs import RETRIEVAL_TRACE_DIR
from docubrain.configs.chat_configs import RETRIEVAL_TRACE_RETENTION_DAYS
from docubrain.utils.logger import setup_logger

logger = setup_logger()

_MAX_CHUNK_PREVIEW_CHARS = 300
_MAX_TRACED_CHUNKS = 50


@dataclass
class TracedChunk:
    document_id: str
    chunk_id: int
    score: float | None
    source: str | None
    semantic_identifier: str | None
    content_preview: str


@dataclass
class RetrievalTrace:
    query: str
    # "query"     -> one record per user turn (authoritative for invocation rate;
    #                written even when retrieval never fires).
    # "retrieval" -> per search-tool call with chunk-level detail.
    trace_type: str = "retrieval"
    search_invoked: bool = False
    generated_queries: list[str] = field(default_factory=list)
    retrieved_chunks: list[TracedChunk] = field(default_factory=list)
    reranked_chunks: list[TracedChunk] = field(default_factory=list)
    chunks_sent_to_llm: list[TracedChunk] = field(default_factory=list)
    final_answer: str | None = None
    answered: bool = False
    retrieval_tool_names: list[str] = field(default_factory=list)
    latency_ms: float | None = None
    reranker_enabled: bool = False
    num_retrieved: int = 0
    num_reranked: int = 0
    num_sent_to_llm: int = 0
    tenant_id: str | None = None
    chat_session_id: str | None = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def record_query_trace(
    query: str,
    *,
    search_invoked: bool,
    retrieval_tool_names: list[str] | None = None,
    answered: bool = False,
    final_answer: str | None = None,
    latency_ms: float | None = None,
    chat_session_id: str | None = None,
    tenant_id: str | None = None,
) -> None:
    """Record one authoritative per-turn trace (audit fix #9 / Phase-4 KPI).

    This is written for EVERY user turn — including turns where retrieval was
    never invoked — so ``% of knowledge queries that invoke retrieval`` can be
    computed directly from the trace log. Best-effort; never raises.
    """
    record_retrieval_trace(
        RetrievalTrace(
            query=query,
            trace_type="query",
            search_invoked=search_invoked,
            retrieval_tool_names=retrieval_tool_names or [],
            answered=answered,
            final_answer=(final_answer[:1000] if final_answer else None),
            latency_ms=latency_ms,
            chat_session_id=chat_session_id,
            tenant_id=tenant_id,
        )
    )


def chunk_to_traced(chunk: Any) -> TracedChunk:
    """Best-effort conversion of an InferenceChunk-like object to a TracedChunk."""
    content = getattr(chunk, "content", "") or ""
    return TracedChunk(
        document_id=getattr(chunk, "document_id", "") or "",
        chunk_id=int(getattr(chunk, "chunk_id", 0) or 0),
        score=getattr(chunk, "score", None),
        source=str(getattr(chunk, "source_type", None) or "") or None,
        semantic_identifier=getattr(chunk, "semantic_identifier", None),
        content_preview=content[:_MAX_CHUNK_PREVIEW_CHARS],
    )


def chunks_to_traced(chunks: list[Any] | None) -> list[TracedChunk]:
    if not chunks:
        return []
    return [chunk_to_traced(c) for c in chunks[:_MAX_TRACED_CHUNKS]]


class RetrievalTraceTimer:
    """Convenience timer: ``with RetrievalTraceTimer() as t: ...; t.elapsed_ms``."""

    def __init__(self) -> None:
        self._start = 0.0
        self.elapsed_ms = 0.0

    def __enter__(self) -> "RetrievalTraceTimer":
        self._start = time.monotonic()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.elapsed_ms = (time.monotonic() - self._start) * 1000.0


def _prune_old_traces(directory: str) -> None:
    cutoff = time.time() - RETRIEVAL_TRACE_RETENTION_DAYS * 86400
    try:
        for name in os.listdir(directory):
            if not name.startswith("retrieval-") or not name.endswith(".jsonl"):
                continue
            path = os.path.join(directory, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except OSError:
                continue
    except OSError:
        pass


def record_retrieval_trace(trace: RetrievalTrace) -> None:
    """Persist a retrieval trace as one JSONL line. Never raises."""
    if not ENABLE_RETRIEVAL_TRACING:
        return
    try:
        # Backfill derived counts if the caller didn't set them.
        trace.num_retrieved = trace.num_retrieved or len(trace.retrieved_chunks)
        trace.num_reranked = trace.num_reranked or len(trace.reranked_chunks)
        trace.num_sent_to_llm = trace.num_sent_to_llm or len(trace.chunks_sent_to_llm)

        os.makedirs(RETRIEVAL_TRACE_DIR, exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = os.path.join(RETRIEVAL_TRACE_DIR, f"retrieval-{day}.jsonl")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(trace.to_json(), ensure_ascii=False) + "\n")
        _prune_old_traces(RETRIEVAL_TRACE_DIR)
    except Exception:
        logger.warning("Failed to write retrieval trace", exc_info=True)
