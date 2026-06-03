import os

PROMPTS_YAML = "./docubrain/seeding/prompts.yaml"
PERSONAS_YAML = "./docubrain/seeding/personas.yaml"
NUM_RETURNED_HITS = 50

# Cross-encoder reranking stage (disabled by default).
ENABLE_RERANKER = os.environ.get("ENABLE_RERANKER", "false").lower() == "true"
# How many reranked chunks to keep and forward to the LLM-selection step.
RERANK_TOP_N = int(os.environ.get("RERANK_TOP_N") or 10)
# Provider: "" / "local" -> self-hosted cross-encoder via the model server
# (Option A, e.g. BAAI/bge-reranker-large); "cohere" / "litellm" / "bedrock"
# route through the corresponding API in RerankingModel (Option B).
RERANKER_PROVIDER = (os.environ.get("RERANKER_PROVIDER") or "").strip().lower()
RERANKER_MODEL = os.environ.get("RERANKER_MODEL") or "BAAI/bge-reranker-large"
RERANKER_API_KEY = os.environ.get("RERANKER_API_KEY") or None
RERANKER_API_URL = os.environ.get("RERANKER_API_URL") or None

# Retrieval observability / tracing (disabled by default, zero overhead).
ENABLE_RETRIEVAL_TRACING = (
    os.environ.get("ENABLE_RETRIEVAL_TRACING", "false").lower() == "true"
)

# ---------------------------------------------------------------------------
# Citation rendering. When false, answers are produced WITHOUT inline source
# links/citations: the citation processor runs in REMOVE mode and the prompt
# no longer instructs the model to cite sources. Default true (unchanged
# behavior). Set SHOW_CITATIONS=false to get clean, link-free answers.
# ---------------------------------------------------------------------------
SHOW_CITATIONS = os.environ.get("SHOW_CITATIONS", "true").lower() == "true"
RETRIEVAL_TRACE_DIR = os.environ.get("RETRIEVAL_TRACE_DIR") or "/var/log/docubrain/retrieval_traces"
RETRIEVAL_TRACE_RETENTION_DAYS = int(
    os.environ.get("RETRIEVAL_TRACE_RETENTION_DAYS") or 7
)

# May be less depending on model
MAX_CHUNKS_FED_TO_CHAT = int(os.environ.get("MAX_CHUNKS_FED_TO_CHAT") or 25)

# 1 / (1 + DOC_TIME_DECAY * doc-age-in-years), set to 0 to have no decay
# Capped in Vespa at 0.5
DOC_TIME_DECAY = float(
    os.environ.get("DOC_TIME_DECAY") or 0.5  # Hits limit at 2 years by default
)
BASE_RECENCY_DECAY = 0.5
FAVOR_RECENT_DECAY_MULTIPLIER = 2.0
# For the highest matching base size chunk, how many chunks above and below do we pull in by default
# Note this is not in any of the deployment configs yet
# Currently only applies to search flow not chat
CONTEXT_CHUNKS_ABOVE = int(os.environ.get("CONTEXT_CHUNKS_ABOVE") or 1)
CONTEXT_CHUNKS_BELOW = int(os.environ.get("CONTEXT_CHUNKS_BELOW") or 1)
# Fairly long but this is to account for edge cases where the LLM pauses for much longer than usual
# The alternative is to fail the request completely so this is intended to be fairly lenient.
LLM_SOCKET_READ_TIMEOUT = int(
    os.environ.get("LLM_SOCKET_READ_TIMEOUT") or "60"
)  # 60 seconds
# Weighting factor between vector and keyword Search; 1 for completely vector
# search, 0 for keyword. Enforces a valid range of [0, 1]. A supplied value from
# the env outside of this range will be clipped to the respective end of the
# range. Defaults to 0.5.
HYBRID_ALPHA = max(0, min(1, float(os.environ.get("HYBRID_ALPHA") or 0.35)))
# Weighting factor between Title and Content of documents during search, 1 for completely
# Title based. Default heavily favors Content because Title is also included at the top of
# Content. This is to avoid cases where the Content is very relevant but it may not be clear
# if the title is separated out. Title is most of a "boost" than a separate field.
TITLE_CONTENT_RATIO = max(
    0, min(1, float(os.environ.get("TITLE_CONTENT_RATIO") or 0.25))
)

# Stops streaming answers back to the UI if this pattern is seen:
STOP_STREAM_PAT = os.environ.get("STOP_STREAM_PAT") or None

# Set this to "true" to hard delete chats
# This will make chats unviewable by admins after a user deletes them
# As opposed to soft deleting them, which just hides them from non-admin users
HARD_DELETE_CHATS = os.environ.get("HARD_DELETE_CHATS", "").lower() == "true"

# Internet Search
NUM_INTERNET_SEARCH_RESULTS = int(os.environ.get("NUM_INTERNET_SEARCH_RESULTS") or 10)
NUM_INTERNET_SEARCH_CHUNKS = int(os.environ.get("NUM_INTERNET_SEARCH_CHUNKS") or 50)

VESPA_SEARCHER_THREADS = int(os.environ.get("VESPA_SEARCHER_THREADS") or 2)

# Whether or not to use the semantic & keyword search expansions for Basic Search
USE_SEMANTIC_KEYWORD_EXPANSIONS_BASIC_SEARCH = (
    os.environ.get("USE_SEMANTIC_KEYWORD_EXPANSIONS_BASIC_SEARCH", "false").lower()
    == "true"
)

# Chat History Compression
# Trigger compression when history exceeds this ratio of available context window
COMPRESSION_TRIGGER_RATIO = float(os.environ.get("COMPRESSION_TRIGGER_RATIO", "0.75"))

SKIP_DEEP_RESEARCH_CLARIFICATION = (
    os.environ.get("SKIP_DEEP_RESEARCH_CLARIFICATION", "false").lower() == "true"
)
