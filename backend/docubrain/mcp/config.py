import os
from dataclasses import dataclass

from docubrain.configs.app_configs import WEB_DOMAIN


DEFAULT_GOOGLE_WORKSPACE_SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
)


@dataclass(frozen=True)
class GoogleWorkspaceSettings:
    enabled: bool
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "GoogleWorkspaceSettings":
        web_domain = WEB_DOMAIN
        client_id = (
            os.environ.get("GOOGLE_WORKSPACE_CLIENT_ID")
            or os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
            or os.environ.get("OAUTH_CLIENT_ID")
            or ""
        )
        client_secret = (
            os.environ.get("GOOGLE_WORKSPACE_CLIENT_SECRET")
            or os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
            or os.environ.get("OAUTH_CLIENT_SECRET")
            or ""
        )

        return cls(
            enabled=(os.environ.get("GOOGLE_WORKSPACE_MCP_ENABLED", "").lower() == "true"),
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=f"{web_domain.rstrip('/')}/mcp/oauth/callback",
            scopes=_parse_scopes(os.environ.get("GOOGLE_WORKSPACE_SCOPES")),
        )


def _parse_scopes(raw_scopes: str | None) -> tuple[str, ...]:
    if not raw_scopes:
        return DEFAULT_GOOGLE_WORKSPACE_SCOPES

    separator = "," if "," in raw_scopes else " "
    scopes = tuple(
        scope.strip()
        for scope in raw_scopes.split(separator)
        if scope.strip()
    )
    return scopes or DEFAULT_GOOGLE_WORKSPACE_SCOPES

