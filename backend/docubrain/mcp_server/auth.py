"""Authentication helpers for the DocuBrain MCP server."""

from typing import Optional

from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.auth import TokenVerifier

from docubrain.mcp.internal_auth import verify_internal_mcp_token
from docubrain.mcp_server.utils import get_http_client
from docubrain.utils.logger import setup_logger
from docubrain.utils.variable_functionality import build_api_server_url_for_http_requests

logger = setup_logger()


class DocubrainTokenVerifier(TokenVerifier):
    """Validates bearer tokens.

    First attempts local verification of internal MCP JWTs (created by
    ``create_internal_mcp_token`` and used for tool calls from the chat
    agent loop).  Falls back to delegating to the API server's ``/me``
    endpoint for regular session tokens.
    """

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        # 1. Try local verification of internal MCP JWTs first.
        internal_claims = verify_internal_mcp_token(token)
        if internal_claims is not None:
            logger.info(
                "Authenticated internal MCP token for user_id=%s",
                internal_claims.user_id,
            )
            return AccessToken(
                token=token,
                client_id="mcp",
                scopes=["mcp:use"],
                expires_at=None,
                resource=None,
                claims={
                    "sub": internal_claims.user_id,
                    "tenant_id": internal_claims.tenant_id,
                },
            )

        # 2. Fall back to API server /me for session tokens.
        try:
            response = await get_http_client().get(
                f"{build_api_server_url_for_http_requests(respect_env_override_if_set=True)}/me",
                headers={"Authorization": f"Bearer {token}"},
            )
        except Exception as exc:
            logger.error(
                "MCP server failed to reach API /me for authentication: %s",
                exc,
                exc_info=True,
            )
            return None

        if response.status_code != 200:
            logger.warning(
                "API server rejected MCP auth token with status %s",
                response.status_code,
            )
            return None

        return AccessToken(
            token=token,
            client_id="mcp",
            scopes=["mcp:use"],
            expires_at=None,
            resource=None,
            claims={},
        )
