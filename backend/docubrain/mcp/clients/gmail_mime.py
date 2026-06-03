import base64
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GmailMessageBody:
    body_text: str | None = None
    body_html: str | None = None


def extract_gmail_message_body(payload: dict[str, Any]) -> GmailMessageBody:
    parts = list(_walk_payload_parts(payload))
    text_values = [
        decoded
        for part in parts
        if part.get("mimeType") == "text/plain"
        if not _is_attachment(part)
        if (decoded := _decode_part_data(part)) is not None
    ]
    html_values = [
        decoded
        for part in parts
        if part.get("mimeType") == "text/html"
        if not _is_attachment(part)
        if (decoded := _decode_part_data(part)) is not None
    ]
    return GmailMessageBody(
        body_text="\n".join(text_values) if text_values else None,
        body_html="\n".join(html_values) if html_values else None,
    )


def _walk_payload_parts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    parts = [payload]
    children = payload.get("parts")
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict):
                parts.extend(_walk_payload_parts(child))
    return parts


def _decode_part_data(part: dict[str, Any]) -> str | None:
    body = part.get("body")
    if not isinstance(body, dict):
        return None
    data = body.get("data")
    if not isinstance(data, str) or not data:
        return None
    padded_data = data + ("=" * ((4 - len(data) % 4) % 4))
    try:
        return base64.urlsafe_b64decode(padded_data).decode("utf-8", errors="replace")
    except Exception:
        return None


def _is_attachment(part: dict[str, Any]) -> bool:
    filename = part.get("filename")
    if isinstance(filename, str) and filename:
        return True
    body = part.get("body")
    return isinstance(body, dict) and isinstance(body.get("attachmentId"), str)
