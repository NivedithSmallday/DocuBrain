from docubrain.mcp.schemas import SanitizedContent


SUSPICIOUS_PROMPT_PATTERNS = (
    "ignore previous instructions",
    "call this tool",
    "system prompt",
    "developer message",
    "oauth token",
    "access token",
    "api key",
    "password",
)


def sanitize_external_content(content: str) -> SanitizedContent:
    normalized_content = content.lower()
    detected_patterns = [
        pattern
        for pattern in SUSPICIOUS_PROMPT_PATTERNS
        if pattern in normalized_content
    ]

    return SanitizedContent(
        content=content,
        is_suspicious=bool(detected_patterns),
        detected_patterns=detected_patterns,
    )
