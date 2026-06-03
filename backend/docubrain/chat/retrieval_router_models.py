"""Models for the hybrid retrieval router.

The router classifies user queries and produces routing decisions that
bias the LLM's tool selection toward the most appropriate retrieval
backend(s): Vespa (internal_search), Google Drive MCP, Gmail MCP, or
a hybrid combination.
"""

from enum import Enum

from pydantic import BaseModel


class QueryCategory(str, Enum):
    """What the user is asking about."""

    EMAIL = "email"
    DRIVE_FILE_LOOKUP = "drive_file_lookup"
    DRIVE_RECENT = "drive_recent"
    SEMANTIC_KNOWLEDGE = "semantic_knowledge"
    METADATA = "metadata"
    MIXED = "mixed"
    CASUAL = "casual"


class RetrievalIntent(str, Enum):
    """What kind of retrieval operation is needed."""

    LOOKUP = "lookup"       # Find a specific file/email
    READ = "read"           # Read/export content of a known file
    SEARCH = "search"       # Broad search across corpus
    BROWSE = "browse"       # List/explore folders, recent files, permissions
    SUMMARIZE = "summarize" # Summarize a document or thread
    NONE = "none"           # No retrieval (casual/greeting)


class RetrievalTarget(str, Enum):
    """Which backend to route to."""

    VESPA = "vespa"
    DRIVE_MCP = "drive_mcp"
    GMAIL_MCP = "gmail_mcp"
    NONE = "none"


class RoutingStrategy(str, Enum):
    """How to execute the retrieval."""

    SINGLE = "single"
    HYBRID = "hybrid"
    FALLBACK_CHAIN = "fallback_chain"
    LLM_DECIDES = "llm_decides"


class ToolPriority(str, Enum):
    """Soft suppression tiers — avoids aggressive filtering."""

    PREFERRED = "preferred"
    NEUTRAL = "neutral"
    DEPRIORITIZED = "deprioritized"
    BLOCKED = "blocked"


class ToolRouting(BaseModel):
    """Per-tool routing priority."""

    tool_name: str
    priority: ToolPriority


class RoutingDecision(BaseModel):
    """The output of the retrieval router."""

    # Classification
    category: QueryCategory
    intent: RetrievalIntent
    freshness_required: bool = False

    # Strategy
    strategy: RoutingStrategy
    primary_targets: list[RetrievalTarget]
    fallback_targets: list[RetrievalTarget] = []

    # Tool prioritization (soft suppression)
    tool_routing: list[ToolRouting] = []
    confidence: float  # 0.0 - 1.0

    # Override (only at very high confidence)
    override_tool_choice: str | None = None  # "required" | "auto" | None

    # Observability
    routing_reason: str

    @property
    def preferred_tool_names(self) -> list[str]:
        return [
            tr.tool_name
            for tr in self.tool_routing
            if tr.priority == ToolPriority.PREFERRED
        ]

    @property
    def blocked_tool_names(self) -> list[str]:
        return [
            tr.tool_name
            for tr in self.tool_routing
            if tr.priority == ToolPriority.BLOCKED
        ]
