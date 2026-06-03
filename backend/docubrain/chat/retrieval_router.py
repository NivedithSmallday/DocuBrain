"""Hybrid retrieval router for DocuBrain.

Classifies user queries using fast heuristics (zero LLM calls, zero
latency) and produces a ``RoutingDecision`` that biases the LLM's
tool selection toward the optimal retrieval backend(s).

The router does NOT replace LLM tool-calling. It constrains and biases
it: narrowing the tool set, injecting prompt hints, and coordinating
fallback when primary retrieval returns empty results.
"""

from __future__ import annotations

import re
from typing import Any

from docubrain.chat.retrieval_router_models import (
    QueryCategory,
    RetrievalIntent,
    RetrievalTarget,
    RoutingDecision,
    RoutingStrategy,
    ToolPriority,
    ToolRouting,
)
from docubrain.utils.logger import setup_logger

logger = setup_logger()

# -----------------------------------------------------------------------
# Canonical tool name sets (kept in sync with google_workspace_oauth.py)
# -----------------------------------------------------------------------

GMAIL_MCP_TOOL_NAMES: frozenset[str] = frozenset({
    "search_gmail",
    "search_emails",
    "get_recent_emails",
    "get_gmail_message",
    "get_email",
    "get_gmail_thread",
    "list_threads",
    "summarize_email_thread",
    "search_attachments",
    "gmail_credential_health",
})

DRIVE_MCP_TOOL_NAMES: frozenset[str] = frozenset({
    "search_google_drive",
    "search_drive_files",
    "search_drive_content",
    "get_drive_file_metadata",
    "read_google_doc",
    "export_google_doc",
    "list_shared_drives",
    "list_recent_files",
    "get_file_permissions",
    "list_folder_contents",
    "google_drive_credential_health",
})

INTERNAL_SEARCH_TOOL_NAME = "internal_search"

# -----------------------------------------------------------------------
# Signal patterns
# -----------------------------------------------------------------------

_EMAIL_SIGNALS = re.compile(
    r"\b(e[-]?mails?|mails?|inbox|gmail|sent\s+me|received|newsletter|"
    r"threads?|attachments?|unread|compose|reply|forward)\b",
    re.IGNORECASE,
)
_EMAIL_ADDRESS_PATTERN = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}")

# Sender/recipient natural-language email signals.
#
# Communication-anchored forms: email vocabulary sits directly next to "from"
# or "me"/"us", so generic phrases like "policy from HR" or "guidance from
# finance" cannot match. Safe to run case-insensitively.
_EMAIL_SENDER_SIGNALS = re.compile(
    r"\b(?:e[-]?mails?|mails?|messages?|msgs?|notes?|pings?|correspondence|threads?)\s+from\b"
    r"|\b(?:hear|heard|hearing)\s+from\b"
    r"|\b(?:e[-]?mail|emailed|message|messaged|msg|ping|pinged|wrote|write|sent|send)\s+(?:me|us)\b"
    r"|\bdid\s+\w+\s+(?:e[-]?mail|message|msg|write|ping|send|sent)\s+(?:me|us)\b",
    re.IGNORECASE,
)

# Bare "anything from <Name>": high false-positive risk, so we require a
# person-pointing lead word AND a Capitalized name token (the name match is
# case-sensitive), AND we exclude common (often-capitalized) department/org
# nouns. This deliberately favors precision: lowercase "anything from sarah"
# is left to the communication-anchored patterns above or the LLM.
_EMAIL_SENDER_PROPER_NOUN = re.compile(
    r"\b(?i:anything|something|everything|nothing|news|word|updates?|repl(?:y|ies))"
    r"\s+from\s+"
    r"(?!(?i:hr|finance|marketing|sales|legal|it|admin|management|engineering|"
    r"ops|operations|support|procurement|accounts|payroll|compliance|security|"
    r"leadership|team|accounting|corporate|the)\b)"
    r"[A-Z][a-z]+",
)

# Direct file references by extension ("budget.xlsx", "onboarding_plan.docx").
# These almost always mean a Drive file and otherwise fall through to
# LLM_DECIDES with no Drive bias.
_FILE_EXTENSION_SIGNALS = re.compile(
    r"\b[\w.-]+\.(?:xlsx?|docx?|pptx?|pdf|csv)\b",
    re.IGNORECASE,
)

_DRIVE_SIGNALS = re.compile(
    r"\b(google\s*drive|google\s*docs?|spreadsheet|google\s*sheets?|"
    r"google\s*slides?|sheets?|shared\s+with\s+me|file\s+permissions?|"
    r"who\s+has\s+access|folder|my\s+files|drive\s+file|"
    r"the\s+\w+\s+doc\b|find\s+the\s+\w+\s+sheet)\b",
    re.IGNORECASE,
)

_INTERNAL_KNOWLEDGE_SIGNALS = re.compile(
    r"\b(hr|human\s+resources|polic(?:y|ies)|leave|leaves|maternity|"
    r"paternity|sick\s+leave|earned\s+leave|attendance|working\s+hours|"
    r"induction|onboarding|employee|employees|role|designation|doj|"
    r"date\s+of\s+joining|company\s+(?:do|does|profile|overview)|"
    r"smallday)\b",
    re.IGNORECASE,
)

_FRESHNESS_SIGNALS = re.compile(
    r"\b(recent|latest|today|yesterday|this\s+week|last\s+week|"
    r"this\s+month|new\s+files?|just\s+(uploaded|shared|created)|"
    r"last\s+modified|updated|current|newest)\b",
    re.IGNORECASE,
)

_METADATA_SIGNALS = re.compile(
    r"\b(permission|shared\s+drives?|who\s+(owns?|shared|created)|"
    r"file\s+size|when\s+was\s+it\s+(modified|created)|"
    r"folder\s+contents?|list\s+files?)\b",
    re.IGNORECASE,
)

_SEMANTIC_STARTERS = re.compile(
    r"^\s*(what|how|why|explain|describe|summarize|tell\s+me\s+about|"
    r"compare|difference\s+between|overview\s+of)\b",
    re.IGNORECASE,
)

_CASUAL_RE = re.compile(
    r"^\s*(hi|hello|hey|thanks|thank\s+you|good\s+(morning|afternoon|evening)|"
    r"bye|goodbye|ok|okay|sure|yes|no|alright)\s*[!.?]*\s*$",
    re.IGNORECASE,
)

# -----------------------------------------------------------------------
# Lightweight intent probes (tool-availability independent)
# -----------------------------------------------------------------------


def query_has_email_intent(query: str) -> bool:
    """True if the query is about email/Gmail (inbox, threads, senders, …).

    Independent of whether any Gmail tool is actually available — used to
    detect "user wants email but no Gmail tool is attached" so we can give an
    honest answer instead of letting the model invent a privacy refusal.
    """
    if not query:
        return False
    return bool(
        _EMAIL_SIGNALS.search(query)
        or _EMAIL_ADDRESS_PATTERN.search(query)
        or _EMAIL_SENDER_SIGNALS.search(query)
        or _EMAIL_SENDER_PROPER_NOUN.search(query)
    )


def query_has_drive_intent(query: str) -> bool:
    """True if the query is about Google Drive / Docs / Sheets / Slides files."""
    if not query:
        return False
    return bool(
        _DRIVE_SIGNALS.search(query) or _FILE_EXTENSION_SIGNALS.search(query)
    )


# -----------------------------------------------------------------------
# Main classifier
# -----------------------------------------------------------------------


def classify_and_route(
    query: str,
    available_tool_names: set[str],
    has_google_workspace: bool,
    has_internal_search: bool,
) -> RoutingDecision:
    """Classify a user query and produce a retrieval routing decision.

    Uses priority-ordered heuristic rules. Zero LLM calls, zero latency.
    """
    query_stripped = query.strip()

    # --- Rule 0: Casual / greeting ---
    if _CASUAL_RE.match(query_stripped):
        decision = _build_decision(
            category=QueryCategory.CASUAL,
            intent=RetrievalIntent.NONE,
            strategy=RoutingStrategy.SINGLE,
            primary_targets=[RetrievalTarget.NONE],
            confidence=1.0,
            reason="Casual greeting — no retrieval needed",
            available_tool_names=available_tool_names,
        )
        _log_decision(query_stripped, decision)
        return decision

    has_email_signal = bool(
        _EMAIL_SIGNALS.search(query_stripped)
        or _EMAIL_ADDRESS_PATTERN.search(query_stripped)
        or _EMAIL_SENDER_SIGNALS.search(query_stripped)
        or _EMAIL_SENDER_PROPER_NOUN.search(query_stripped)
    )
    has_drive_signal = bool(
        _DRIVE_SIGNALS.search(query_stripped)
        or _FILE_EXTENSION_SIGNALS.search(query_stripped)
    )
    has_freshness_signal = bool(_FRESHNESS_SIGNALS.search(query_stripped))
    has_metadata_signal = bool(_METADATA_SIGNALS.search(query_stripped))
    has_internal_knowledge_signal = bool(
        _INTERNAL_KNOWLEDGE_SIGNALS.search(query_stripped)
    )
    has_semantic_signal = bool(
        _SEMANTIC_STARTERS.match(query_stripped)
        or len(query_stripped.split()) > 8
        or has_internal_knowledge_signal
    )

    # --- Rule 1: Email queries → Gmail MCP ---
    if has_email_signal and has_google_workspace:
        intent = RetrievalIntent.SEARCH
        if any(w in query_stripped.lower() for w in ("read", "open", "show", "get")):
            intent = RetrievalIntent.READ
        elif "summarize" in query_stripped.lower():
            intent = RetrievalIntent.SUMMARIZE

        decision = _build_decision(
            category=QueryCategory.EMAIL,
            intent=intent,
            strategy=RoutingStrategy.SINGLE,
            primary_targets=[RetrievalTarget.GMAIL_MCP],
            confidence=0.9,
            reason="Email-related signals detected",
            available_tool_names=available_tool_names,
            has_internal_search=has_internal_search,
            freshness_required=has_freshness_signal,
            preferred_targets={RetrievalTarget.GMAIL_MCP},
            deprioritized_targets={RetrievalTarget.VESPA},
        )
        _log_decision(query_stripped, decision)
        return decision

    # --- Rule 2: Metadata queries → Drive MCP ---
    if has_metadata_signal and has_google_workspace:
        decision = _build_decision(
            category=QueryCategory.METADATA,
            intent=RetrievalIntent.BROWSE,
            strategy=RoutingStrategy.SINGLE,
            primary_targets=[RetrievalTarget.DRIVE_MCP],
            confidence=0.85,
            reason="Metadata/permissions signals detected",
            available_tool_names=available_tool_names,
            has_internal_search=has_internal_search,
            preferred_targets={RetrievalTarget.DRIVE_MCP},
            deprioritized_targets={RetrievalTarget.VESPA},
        )
        _log_decision(query_stripped, decision)
        return decision

    # --- Rule 3: Drive-specific file lookup → Drive MCP ---
    if has_drive_signal and has_google_workspace and not has_semantic_signal:
        intent = RetrievalIntent.LOOKUP
        if any(w in query_stripped.lower() for w in ("read", "open", "content", "export")):
            intent = RetrievalIntent.READ
        elif any(w in query_stripped.lower() for w in ("summarize", "summary")):
            intent = RetrievalIntent.SUMMARIZE

        primary_targets = (
            [RetrievalTarget.VESPA]
            if has_internal_search
            else [RetrievalTarget.DRIVE_MCP]
        )
        fallback_targets = [RetrievalTarget.DRIVE_MCP] if has_internal_search else []
        preferred_targets = (
            {RetrievalTarget.VESPA}
            if has_internal_search
            else {RetrievalTarget.DRIVE_MCP}
        )

        decision = _build_decision(
            category=QueryCategory.DRIVE_FILE_LOOKUP,
            intent=intent,
            strategy=RoutingStrategy.FALLBACK_CHAIN
            if has_internal_search
            else RoutingStrategy.SINGLE,
            primary_targets=primary_targets,
            fallback_targets=fallback_targets,
            confidence=0.85,
            reason="Drive-specific file signals detected; indexed search preferred when available",
            available_tool_names=available_tool_names,
            has_internal_search=has_internal_search,
            freshness_required=has_freshness_signal,
            preferred_targets=preferred_targets,
        )
        _log_decision(query_stripped, decision)
        return decision

    # --- Rule 3b: Internal HR/company/employee knowledge -> Vespa + Drive parallel ---
    if has_internal_knowledge_signal and has_internal_search:
        decision = _build_decision(
            category=QueryCategory.SEMANTIC_KNOWLEDGE,
            intent=RetrievalIntent.SEARCH,
            strategy=RoutingStrategy.HYBRID
            if has_google_workspace
            else RoutingStrategy.SINGLE,
            primary_targets=(
                [RetrievalTarget.VESPA, RetrievalTarget.DRIVE_MCP]
                if has_google_workspace
                else [RetrievalTarget.VESPA]
            ),
            confidence=0.85,
            reason="Internal HR/company/employee signal — parallel Vespa + Drive retrieval",
            available_tool_names=available_tool_names,
            has_internal_search=has_internal_search,
            preferred_targets={RetrievalTarget.VESPA},
        )
        _log_decision(query_stripped, decision)
        return decision

    # --- Rule 4: Recency queries → Drive MCP with Vespa fallback ---
    if has_freshness_signal and has_google_workspace:
        primary_targets = (
            [RetrievalTarget.VESPA]
            if has_internal_search
            else [RetrievalTarget.DRIVE_MCP]
        )
        fallback_targets = [RetrievalTarget.DRIVE_MCP] if has_internal_search else []
        preferred_targets = (
            {RetrievalTarget.VESPA}
            if has_internal_search
            else {RetrievalTarget.DRIVE_MCP}
        )

        decision = _build_decision(
            category=QueryCategory.DRIVE_RECENT,
            intent=RetrievalIntent.SEARCH,
            strategy=RoutingStrategy.FALLBACK_CHAIN,
            primary_targets=primary_targets,
            fallback_targets=fallback_targets,
            confidence=0.8,
            reason="Recency signals detected — live Drive MCP preferred over stale index",
            available_tool_names=available_tool_names,
            has_internal_search=has_internal_search,
            freshness_required=True,
            preferred_targets=preferred_targets,
        )
        _log_decision(query_stripped, decision)
        return decision

    # --- Rule 5: Mixed (Drive + semantic) → Hybrid ---
    if has_drive_signal and has_semantic_signal and has_google_workspace:
        decision = _build_decision(
            category=QueryCategory.MIXED,
            intent=RetrievalIntent.SEARCH,
            strategy=RoutingStrategy.HYBRID,
            primary_targets=[RetrievalTarget.VESPA, RetrievalTarget.DRIVE_MCP],
            confidence=0.6,
            reason="Both file-specific and semantic signals — hybrid retrieval recommended",
            available_tool_names=available_tool_names,
            has_internal_search=has_internal_search,
            freshness_required=has_freshness_signal,
            preferred_targets={RetrievalTarget.VESPA, RetrievalTarget.DRIVE_MCP},
        )
        _log_decision(query_stripped, decision)
        return decision

    # --- Rule 6: Pure semantic / knowledge query → Vespa (with Drive supplement) ---
    if has_semantic_signal and has_internal_search:
        decision = _build_decision(
            category=QueryCategory.SEMANTIC_KNOWLEDGE,
            intent=RetrievalIntent.SEARCH,
            strategy=RoutingStrategy.SINGLE
            if not has_google_workspace
            else RoutingStrategy.HYBRID,
            primary_targets=(
                [RetrievalTarget.VESPA, RetrievalTarget.DRIVE_MCP]
                if has_google_workspace
                else [RetrievalTarget.VESPA]
            ),
            confidence=0.7,
            reason="Semantic/knowledge query — Vespa index preferred, Drive supplemental",
            available_tool_names=available_tool_names,
            has_internal_search=has_internal_search,
            preferred_targets={RetrievalTarget.VESPA},
        )
        _log_decision(query_stripped, decision)
        return decision

    # --- Rule 7: Default — let LLM decide ---
    decision = _build_decision(
        category=QueryCategory.MIXED if (has_drive_signal or has_email_signal) else QueryCategory.SEMANTIC_KNOWLEDGE,
        intent=RetrievalIntent.SEARCH,
        strategy=RoutingStrategy.LLM_DECIDES,
        primary_targets=[RetrievalTarget.VESPA],
        confidence=0.3,
        reason="Low-confidence classification — LLM will decide tool selection",
        available_tool_names=available_tool_names,
        has_internal_search=has_internal_search,
    )
    _log_decision(query_stripped, decision)
    return decision


# -----------------------------------------------------------------------
# Decision builder
# -----------------------------------------------------------------------


def _build_decision(
    *,
    category: QueryCategory,
    intent: RetrievalIntent,
    strategy: RoutingStrategy,
    primary_targets: list[RetrievalTarget],
    confidence: float,
    reason: str,
    available_tool_names: set[str],
    has_internal_search: bool = False,
    fallback_targets: list[RetrievalTarget] | None = None,
    freshness_required: bool = False,
    preferred_targets: set[RetrievalTarget] | None = None,
    deprioritized_targets: set[RetrievalTarget] | None = None,
) -> RoutingDecision:
    """Build a RoutingDecision with soft tool prioritization.

    INVARIANT (audit fix #4): when an internal-search (Vespa) tool is available,
    Vespa MUST participate in retrieval. The router may *prioritize* other tools,
    but it must NEVER exclude or deprioritize Vespa — doing so caused indexed
    documents to never be searched for Drive/metadata/email queries.
    """
    preferred = set(preferred_targets or set())
    deprioritized = set(deprioritized_targets or set())

    primary_targets = list(primary_targets)

    if has_internal_search:
        # 1. Vespa is never deprioritized.
        deprioritized.discard(RetrievalTarget.VESPA)
        # 2. Vespa always participates in primary retrieval (run in parallel).
        if RetrievalTarget.VESPA not in primary_targets:
            primary_targets.append(RetrievalTarget.VESPA)
            # Upgrade a single-target plan to parallel hybrid now that Vespa
            # has been added alongside the originally-preferred target(s).
            if strategy == RoutingStrategy.SINGLE:
                strategy = RoutingStrategy.HYBRID
        # 3. Vespa must never be a deprioritized/blocked NEUTRAL afterthought
        #    when nothing else is preferred — keep at least NEUTRAL.

    tool_routing: list[ToolRouting] = []
    for tool_name in available_tool_names:
        target = _tool_name_to_target(tool_name)
        if target in preferred:
            priority = ToolPriority.PREFERRED
        elif target in deprioritized:
            priority = ToolPriority.DEPRIORITIZED
        else:
            priority = ToolPriority.NEUTRAL
        tool_routing.append(ToolRouting(tool_name=tool_name, priority=priority))

    return RoutingDecision(
        category=category,
        intent=intent,
        freshness_required=freshness_required,
        strategy=strategy,
        primary_targets=primary_targets,
        fallback_targets=fallback_targets or [],
        tool_routing=tool_routing,
        confidence=confidence,
        routing_reason=reason,
    )


def _tool_name_to_target(tool_name: str) -> RetrievalTarget:
    """Map a tool name to its retrieval target."""
    if tool_name in GMAIL_MCP_TOOL_NAMES:
        return RetrievalTarget.GMAIL_MCP
    if tool_name in DRIVE_MCP_TOOL_NAMES:
        return RetrievalTarget.DRIVE_MCP
    if tool_name == INTERNAL_SEARCH_TOOL_NAME:
        return RetrievalTarget.VESPA
    return RetrievalTarget.NONE


# -----------------------------------------------------------------------
# Fallback helpers
# -----------------------------------------------------------------------


def tool_responses_are_empty(tool_responses: list[Any]) -> bool:
    """Check if all tool responses returned empty/no results.

    Used by the fallback chain to decide whether to retry with
    secondary targets.
    """
    for tr in tool_responses:
        rich = getattr(tr, "rich_response", None)
        if rich is None:
            continue

        # SearchDocsResponse (internal_search)
        search_docs = getattr(rich, "search_docs", None)
        if search_docs is not None:
            if search_docs:
                return False
            continue

        # CustomToolCallSummary (MCP tools)
        tool_result = getattr(rich, "tool_result", None)
        if tool_result is not None:
            if isinstance(tool_result, dict):
                for key in ("results", "files", "messages", "records", "threads"):
                    val = tool_result.get(key)
                    if val:
                        return False
                if tool_result.get("error"):
                    continue
                # Non-empty dict with data
                if any(
                    v for k, v in tool_result.items()
                    if k not in ("request_id", "tool", "provider", "truncation")
                    and v
                ):
                    return False
            elif tool_result:
                return False

    return True


def build_fallback_decision(original: RoutingDecision) -> RoutingDecision | None:
    """Create a new decision using fallback targets, or None if no fallback."""
    if not original.fallback_targets:
        return None
    return RoutingDecision(
        category=original.category,
        intent=original.intent,
        freshness_required=original.freshness_required,
        strategy=RoutingStrategy.SINGLE,
        primary_targets=original.fallback_targets,
        fallback_targets=[],
        tool_routing=original.tool_routing,
        confidence=original.confidence * 0.8,
        routing_reason=f"Fallback: primary returned empty, trying {original.fallback_targets}",
    )


# -----------------------------------------------------------------------
# Observability
# -----------------------------------------------------------------------


def _log_decision(query: str, decision: RoutingDecision) -> None:
    """Structured log for every routing decision."""
    query_preview = query[:80] + "..." if len(query) > 80 else query
    logger.info(
        "RetrievalRouter: category=%s intent=%s strategy=%s confidence=%.2f "
        "freshness=%s targets=%s fallback=%s preferred=%s reason=%s query=%r",
        decision.category.value,
        decision.intent.value,
        decision.strategy.value,
        decision.confidence,
        decision.freshness_required,
        [t.value for t in decision.primary_targets],
        [t.value for t in decision.fallback_targets],
        decision.preferred_tool_names,
        decision.routing_reason,
        query_preview,
    )

    # Warn when Vespa is excluded from targets — potential retrieval gap
    vespa_included = any(
        t == RetrievalTarget.VESPA for t in decision.primary_targets
    ) or any(t == RetrievalTarget.VESPA for t in decision.fallback_targets)
    if not vespa_included and decision.category not in (
        QueryCategory.CASUAL,
        QueryCategory.EMAIL,
        QueryCategory.METADATA,
    ):
        logger.warning(
            "RetrievalRouter: VESPA_BYPASS query=%r routed to %s only — "
            "indexed content search skipped. category=%s",
            query_preview,
            [t.value for t in decision.primary_targets],
            decision.category.value,
        )
