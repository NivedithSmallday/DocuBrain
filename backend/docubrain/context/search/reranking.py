"""Cross-encoder reranking stage (audit fix #3).

Sits between hybrid retrieval and the LLM chunk-selection step:

    Hybrid Retrieval (Top N) -> Cross-Encoder Reranker -> Top K -> LLM Selection

The reranker is feature-flagged via ``ENABLE_RERANKER`` and defaults to OFF, so
existing behavior is unchanged until it is explicitly enabled. Any failure
(model server down, missing API key, dependency missing) degrades gracefully:
the original retrieval order is returned and a warning is logged. This keeps the
search path resilient and fully backward compatible.

Option A (preferred): a self-hosted cross-encoder such as ``BAAI/bge-reranker-large``
served by the model server (``RERANKER_PROVIDER`` empty/"local").
Option B: a hosted reranker (Cohere / LiteLLM / Bedrock) via ``RERANKER_PROVIDER``.
"""

from __future__ import annotations

from docubrain.configs.chat_configs import ENABLE_RERANKER
from docubrain.configs.chat_configs import RERANK_TOP_N
from docubrain.configs.chat_configs import RERANKER_API_KEY
from docubrain.configs.chat_configs import RERANKER_API_URL
from docubrain.configs.chat_configs import RERANKER_MODEL
from docubrain.configs.chat_configs import RERANKER_PROVIDER
from docubrain.context.search.models import InferenceChunk
from docubrain.utils.logger import setup_logger

logger = setup_logger()

# Cap passage text length sent to the reranker to keep latency bounded; the
# cross-encoder truncates internally anyway.
_MAX_PASSAGE_CHARS = 2048


def _provider_type() -> "object | None":
    """Map RERANKER_PROVIDER to a RerankerProvider enum (or None for local)."""
    if not RERANKER_PROVIDER or RERANKER_PROVIDER in ("local", "model_server"):
        return None
    # Imported lazily so this module has no hard dependency on the enum at import.
    from shared_configs.enums import RerankerProvider

    try:
        return RerankerProvider(RERANKER_PROVIDER)
    except ValueError:
        logger.warning(
            "RERANKER_PROVIDER=%r is not a recognized provider; "
            "falling back to local model-server reranking.",
            RERANKER_PROVIDER,
        )
        return None


def rerank_inference_chunks(
    query: str,
    chunks: list[InferenceChunk],
    top_n: int = RERANK_TOP_N,
    *,
    enabled: bool = ENABLE_RERANKER,
) -> list[InferenceChunk]:
    """Rerank ``chunks`` against ``query`` with a cross-encoder, keep top ``top_n``.

    Returns chunks ordered by descending cross-encoder relevance, with each
    chunk's ``score`` overwritten by the rerank score. On any error or when
    disabled, returns the input order unchanged (truncated to ``top_n`` only when
    reranking actually ran).
    """
    if not enabled:
        return chunks
    if not query or not chunks:
        return chunks
    if len(chunks) == 1:
        return chunks

    passages = [(c.content or "")[:_MAX_PASSAGE_CHARS] for c in chunks]

    try:
        # Imported lazily to avoid pulling heavy ML deps at module import time.
        from docubrain.natural_language_processing.search_nlp_models import (
            RerankingModel,
        )

        model = RerankingModel(
            model_name=RERANKER_MODEL,
            provider_type=_provider_type(),
            api_key=RERANKER_API_KEY,
            api_url=RERANKER_API_URL,
        )
        scores = model.predict(query=query, passages=passages)
    except Exception:
        logger.warning(
            "Cross-encoder reranking failed; returning original retrieval order. "
            "query=%r n_chunks=%d model=%s provider=%s",
            (query[:80] if query else query),
            len(chunks),
            RERANKER_MODEL,
            RERANKER_PROVIDER or "local",
            exc_info=True,
        )
        return chunks

    if not scores or len(scores) != len(chunks):
        logger.warning(
            "Reranker returned %d scores for %d chunks; skipping rerank.",
            len(scores) if scores else 0,
            len(chunks),
        )
        return chunks

    rescored: list[InferenceChunk] = []
    for chunk, score in zip(chunks, scores):
        chunk.score = float(score)
        rescored.append(chunk)

    rescored.sort(key=lambda c: c.score if c.score is not None else float("-inf"), reverse=True)

    kept = rescored[:top_n] if top_n and top_n > 0 else rescored
    logger.info(
        "Reranked %d chunks -> kept top %d (model=%s, provider=%s)",
        len(chunks),
        len(kept),
        RERANKER_MODEL,
        RERANKER_PROVIDER or "local",
    )
    return kept
