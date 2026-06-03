"""Response size protection and request validation for Google Workspace MCP.

All limits are module-level constants for easy override in tests or future
config-driven deployment. Truncation helpers always return metadata about
whether truncation occurred and the original vs returned sizes.
"""

from docubrain.error_handling.error_codes import DocubrainErrorCode
from docubrain.error_handling.exceptions import DocubrainError

# ---------------------------------------------------------------------------
# Configurable limits
# ---------------------------------------------------------------------------

MAX_QUERY_LENGTH: int = 1000
MAX_PAGE_TOKEN_LENGTH: int = 500
MAX_ID_LENGTH: int = 100
MAX_GMAIL_BODY_CHARS: int = 50_000
MAX_THREAD_MESSAGES: int = 50
MAX_MCP_RESPONSE_ESTIMATED_CHARS: int = 500_000
MAX_ATTACHMENT_METADATA_COUNT: int = 100

TRUNCATION_MARKER: str = "\n\n[truncated]"


# ---------------------------------------------------------------------------
# Truncation helpers
# ---------------------------------------------------------------------------


class TruncationResult:
    """Immutable container for a possibly-truncated string with metadata."""

    __slots__ = ("text", "is_truncated", "original_size", "returned_size")

    def __init__(
        self,
        text: str | None,
        is_truncated: bool,
        original_size: int,
        returned_size: int,
    ) -> None:
        self.text = text
        self.is_truncated = is_truncated
        self.original_size = original_size
        self.returned_size = returned_size


def truncate_body(
    text: str | None,
    max_chars: int = MAX_GMAIL_BODY_CHARS,
) -> TruncationResult:
    """Truncate a body string and return metadata about the truncation."""
    if text is None:
        return TruncationResult(
            text=None,
            is_truncated=False,
            original_size=0,
            returned_size=0,
        )
    original_size = len(text)
    if original_size <= max_chars:
        return TruncationResult(
            text=text,
            is_truncated=False,
            original_size=original_size,
            returned_size=original_size,
        )
    # Leave room for the truncation marker.  If max_chars is smaller than
    # the marker itself, hard-cut without appending the marker.
    cut_point = max_chars - len(TRUNCATION_MARKER)
    if cut_point < 0:
        truncated = text[:max_chars]
    else:
        truncated = text[:cut_point] + TRUNCATION_MARKER
    return TruncationResult(
        text=truncated,
        is_truncated=True,
        original_size=original_size,
        returned_size=len(truncated),
    )


def cap_thread_messages(
    messages: list[dict[str, object]],
    max_messages: int = MAX_THREAD_MESSAGES,
) -> tuple[list[dict[str, object]], bool, int]:
    """Cap a thread message list and return (capped_list, was_capped, original_count)."""
    original_count = len(messages)
    if original_count <= max_messages:
        return messages, False, original_count
    return messages[:max_messages], True, original_count


def estimate_response_chars(payload: dict[str, object]) -> int:
    """Rough character estimate for a response payload before serialization."""
    # Fast heuristic: convert to string and measure length.
    # This is intentionally cheap — not a full JSON serialization.
    return len(str(payload))


def check_response_size(
    payload: dict[str, object],
    max_chars: int = MAX_MCP_RESPONSE_ESTIMATED_CHARS,
) -> bool:
    """Return True if the estimated response size is within limits."""
    return estimate_response_chars(payload) <= max_chars


# ---------------------------------------------------------------------------
# Request validation helpers
# ---------------------------------------------------------------------------


def validate_query(query: str) -> str:
    """Validate and return a stripped query string.

    Raises DocubrainError on empty or oversized queries.
    """
    stripped = query.strip()
    if not stripped:
        raise DocubrainError(
            DocubrainErrorCode.INVALID_INPUT,
            "Search query must be non-empty",
        )
    if len(stripped) > MAX_QUERY_LENGTH:
        raise DocubrainError(
            DocubrainErrorCode.INVALID_INPUT,
            f"Search query exceeds maximum length of {MAX_QUERY_LENGTH} characters",
        )
    return stripped


def validate_id(value: str, field_name: str) -> str:
    """Validate a Google message/thread/file ID.

    Raises DocubrainError on empty, oversized, or non-alphanumeric IDs.
    """
    stripped = value.strip()
    if not stripped:
        raise DocubrainError(
            DocubrainErrorCode.INVALID_INPUT,
            f"{field_name} must be non-empty",
        )
    if len(stripped) > MAX_ID_LENGTH:
        raise DocubrainError(
            DocubrainErrorCode.INVALID_INPUT,
            f"{field_name} exceeds maximum length of {MAX_ID_LENGTH} characters",
        )
    # Google IDs are alphanumeric with occasional hyphens/underscores
    if not all(c.isalnum() or c in ("-", "_") for c in stripped):
        raise DocubrainError(
            DocubrainErrorCode.INVALID_INPUT,
            f"{field_name} contains invalid characters",
        )
    return stripped


def validate_page_token(page_token: str | None) -> str | None:
    """Validate an optional pagination token."""
    if page_token is None:
        return None
    stripped = page_token.strip()
    if not stripped:
        return None
    if len(stripped) > MAX_PAGE_TOKEN_LENGTH:
        raise DocubrainError(
            DocubrainErrorCode.INVALID_INPUT,
            f"Page token exceeds maximum length of {MAX_PAGE_TOKEN_LENGTH} characters",
        )
    return stripped


def validate_limit(limit: int) -> int:
    """Clamp limit to [1, 25]."""
    return max(1, min(limit, 25))
