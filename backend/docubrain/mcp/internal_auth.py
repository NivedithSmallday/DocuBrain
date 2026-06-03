from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any

import jwt

from docubrain.configs.app_configs import USER_AUTH_SECRET

INTERNAL_MCP_AUDIENCE = "docubrain:internal-mcp"
INTERNAL_MCP_ISSUER = "docubrain"
INTERNAL_MCP_TOKEN_TYPE = "internal_mcp"
INTERNAL_MCP_TOKEN_LIFETIME_SECONDS = 60 * 60  # 1 hour for long agentic conversations
INTERNAL_MCP_TOKEN_REFRESH_BUFFER_SECONDS = 5 * 60  # refresh 5 min before expiry


@dataclass(frozen=True)
class InternalMCPTokenClaims:
    user_id: str
    tenant_id: str | None


def create_internal_mcp_token(
    user: Any,
    *,
    tenant_id: str | None,
    lifetime_seconds: int = INTERNAL_MCP_TOKEN_LIFETIME_SECONDS,
) -> str:
    if not USER_AUTH_SECRET:
        raise ValueError("USER_AUTH_SECRET is required for internal MCP auth")

    now = datetime.now(timezone.utc)
    payload = {
        "iss": INTERNAL_MCP_ISSUER,
        "aud": INTERNAL_MCP_AUDIENCE,
        "typ": INTERNAL_MCP_TOKEN_TYPE,
        "sub": str(user.id),
        "tenant_id": tenant_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=lifetime_seconds)).timestamp()),
    }
    return jwt.encode(payload, USER_AUTH_SECRET, algorithm="HS256")


def verify_internal_mcp_token(token: str) -> InternalMCPTokenClaims | None:
    if not USER_AUTH_SECRET:
        return None

    try:
        payload = jwt.decode(
            token,
            USER_AUTH_SECRET,
            algorithms=["HS256"],
            audience=INTERNAL_MCP_AUDIENCE,
            issuer=INTERNAL_MCP_ISSUER,
        )
    except jwt.PyJWTError:
        return None

    if payload.get("typ") != INTERNAL_MCP_TOKEN_TYPE:
        return None
    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id:
        return None
    tenant_id = payload.get("tenant_id")
    return InternalMCPTokenClaims(
        user_id=user_id,
        tenant_id=tenant_id if isinstance(tenant_id, str) else None,
    )


def internal_mcp_token_needs_refresh(token: str) -> bool:
    """Check if an internal MCP token is close to expiry and should be refreshed.

    Returns True if the token will expire within INTERNAL_MCP_TOKEN_REFRESH_BUFFER_SECONDS.
    """
    if not USER_AUTH_SECRET:
        return True

    try:
        payload = jwt.decode(
            token,
            USER_AUTH_SECRET,
            algorithms=["HS256"],
            audience=INTERNAL_MCP_AUDIENCE,
            issuer=INTERNAL_MCP_ISSUER,
            options={"verify_exp": False},
        )
    except jwt.PyJWTError:
        return True

    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        return True
    now = datetime.now(timezone.utc).timestamp()
    return exp - now < INTERNAL_MCP_TOKEN_REFRESH_BUFFER_SECONDS
