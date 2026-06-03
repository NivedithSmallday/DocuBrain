from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC
from datetime import datetime
from typing import Any
from uuid import uuid4


SENSITIVE_AUDIT_METADATA_KEYS = {
    "access_token",
    "refresh_token",
    "token",
    "client_secret",
    "email_body",
    "body",
    "file_body",
    "file_content",
    "content_body",
    "raw_payload",
    "google_payload",
}


def get_or_create_request_id(request_id: str | None) -> str:
    if request_id and request_id.strip():
        return request_id.strip()
    return uuid4().hex


def build_safe_audit_event(
    *,
    user_id: str,
    provider: str,
    tool: str,
    request_id: str,
    success: bool,
    upstream_status_code: int | None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event": "google_workspace_mcp_audit",
        "timestamp": datetime.now(UTC).isoformat(),
        "user_id": user_id,
        "provider": provider,
        "tool": tool,
        "request_id": request_id,
        "success": success,
        "upstream_status_code": upstream_status_code,
    }
    safe_metadata = _safe_metadata(metadata or {})
    if safe_metadata:
        event["metadata"] = safe_metadata
    return event


def record_google_workspace_metric(
    *,
    provider: str,
    operation: str,
    success: bool,
    duration_seconds: float,
) -> None:
    # Hook for Prometheus counters/histograms.
    _ = (provider, operation, success, duration_seconds)


def record_google_workspace_latency(
    *,
    provider: str,
    operation: str,
    duration_seconds: float,
    request_id: str,
) -> None:
    """Hook for latency histogram recording."""
    _ = (provider, operation, duration_seconds, request_id)


def record_google_workspace_refresh(
    *,
    provider: str,
    success: bool,
    request_id: str,
) -> None:
    """Hook for refresh success/failure counter."""
    _ = (provider, success, request_id)


def record_google_workspace_pagination(
    *,
    provider: str,
    operation: str,
    page_depth: int,
    request_id: str,
) -> None:
    """Hook for pagination depth counter."""
    _ = (provider, operation, page_depth, request_id)


def record_google_workspace_quota_event(
    *,
    provider: str,
    status_code: int,
    request_id: str,
    retry_exhausted: bool = False,
) -> None:
    """Hook for upstream 429/quota counter and retry exhaustion tracking."""
    _ = (provider, status_code, request_id, retry_exhausted)


def record_google_workspace_timeout(
    *,
    provider: str,
    operation: str,
    timeout_seconds: float,
    request_id: str,
) -> None:
    """Hook for upstream timeout classification in metrics/audit."""
    _ = (provider, operation, timeout_seconds, request_id)


@contextmanager
def trace_google_workspace_operation(
    *,
    provider: str,
    operation: str,
    request_id: str,
) -> Iterator[None]:
    # Hook for OpenTelemetry spans.
    _ = (provider, operation, request_id)
    yield


def emit_audit_log(event: dict[str, Any]) -> None:
    """Emit a structured audit log event through the standard logger.

    The logger prefix ``google_workspace_mcp_audit`` enables easy filtering
    in log aggregation systems.
    """
    import logging

    logger = logging.getLogger("google_workspace_mcp_audit")
    logger.info("%s", event)


def _safe_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in metadata.items()
        if key.lower() not in SENSITIVE_AUDIT_METADATA_KEYS
    }
