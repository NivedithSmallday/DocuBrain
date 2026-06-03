"""Default MCP server bootstrap for workspace/admin onboarding."""

from dataclasses import dataclass
from typing import Callable
from typing import Protocol

from sqlalchemy.orm import Session

from docubrain.auth.schemas import UserRole
from docubrain.configs.app_configs import MCP_DEFAULT_SERVER_URL
from docubrain.configs.app_configs import MCP_SERVER_ENABLED
from docubrain.db.enums import MCPAuthenticationPerformer
from docubrain.db.enums import MCPAuthenticationType
from docubrain.db.enums import MCPServerStatus
from docubrain.db.enums import MCPTransport
from docubrain.db.mcp import create_mcp_server__no_commit
from docubrain.db.mcp import get_all_mcp_servers
from docubrain.db.tools import create_tool__no_commit
from docubrain.db.tools import delete_tool__no_commit
from docubrain.db.tools import get_tools_by_mcp_server_id
from docubrain.db.models import MCPServer
from docubrain.db.models import Tool
from docubrain.db.models import User
from docubrain.utils.logger import setup_logger
from docubrain.utils.threadpool_concurrency import run_async_sync_no_cancel

logger = setup_logger()

DEFAULT_DOCUBRAIN_MCP_SERVER_NAME = "Google Workspace MCP"
DEFAULT_DOCUBRAIN_MCP_SERVER_DESCRIPTION = (
    "Built-in DocuBrain MCP server for Google Workspace tools."
)
DEFAULT_DOCUBRAIN_MCP_SERVER_URL = MCP_DEFAULT_SERVER_URL


class DiscoveredMCPTool(Protocol):
    name: str
    description: str | None
    title: str | None
    inputSchema: dict


@dataclass(frozen=True)
class MCPBootstrapResult:
    created_servers: int = 0
    created_tools: int = 0
    updated_tools: int = 0
    deleted_tools: int = 0
    skipped: bool = False


def discover_local_docubrain_mcp_tools() -> list[DiscoveredMCPTool]:
    """Read the built-in DocuBrain MCP tool registry without network/auth tokens.

    FastMCP's list_tools() returns FunctionTool objects whose input schema lives
    under ``.parameters``, not ``.inputSchema``.  Converting each FunctionTool to
    the canonical ``mcp.types.Tool`` via ``to_mcp_tool()`` gives us the standard
    ``.inputSchema`` attribute that the rest of the bootstrap pipeline expects.
    """
    from docubrain.mcp_server.api import mcp_server

    function_tools = run_async_sync_no_cancel(mcp_server.list_tools())
    return [ft.to_mcp_tool() for ft in function_tools]


def bootstrap_default_mcp_servers_for_admin(
    db_session: Session,
    user: User,
    *,
    discover_tools: Callable[[], list[DiscoveredMCPTool]] = discover_local_docubrain_mcp_tools,
    server_url: str = DEFAULT_DOCUBRAIN_MCP_SERVER_URL,
    force: bool = False,
) -> MCPBootstrapResult:
    """Ensure default MCP servers and tool rows exist for an admin workspace.

    This is idempotent by server name and URL. It intentionally does not persist
    access tokens; default DocuBrain MCP tool schemas are discovered in-process.
    """
    if not MCP_SERVER_ENABLED:
        return MCPBootstrapResult(skipped=True)

    if not force and user.role != UserRole.ADMIN:
        return MCPBootstrapResult(skipped=True)

    servers = get_all_mcp_servers(db_session)
    server = _find_default_server(servers, server_url)
    created_servers = 0

    if server is None:
        server = create_mcp_server__no_commit(
            owner_email=user.email,
            name=DEFAULT_DOCUBRAIN_MCP_SERVER_NAME,
            description=DEFAULT_DOCUBRAIN_MCP_SERVER_DESCRIPTION,
            server_url=server_url,
            auth_type=MCPAuthenticationType.PT_OAUTH,
            transport=MCPTransport.STREAMABLE_HTTP,
            auth_performer=MCPAuthenticationPerformer.PER_USER,
            db_session=db_session,
        )
        created_servers = 1
    else:
        server.server_url = server_url
        server.description = DEFAULT_DOCUBRAIN_MCP_SERVER_DESCRIPTION
        server.auth_type = MCPAuthenticationType.PT_OAUTH
        server.transport = MCPTransport.STREAMABLE_HTTP
        server.auth_performer = MCPAuthenticationPerformer.PER_USER

    discovered_tools = discover_tools()
    created_tools, updated_tools, deleted_tools = _sync_tools_for_server(
        db_session,
        server,
        discovered_tools,
    )

    server.status = MCPServerStatus.CONNECTED
    db_session.commit()

    # Assign all Google Workspace MCP tools to the default persona so they
    # are available in normal chat sessions (not just the agent editor).
    _assign_mcp_tools_to_default_persona(db_session, server)

    logger.info(
        "Bootstrapped default MCP server '%s': created_servers=%s created_tools=%s "
        "updated_tools=%s deleted_tools=%s",
        DEFAULT_DOCUBRAIN_MCP_SERVER_NAME,
        created_servers,
        created_tools,
        updated_tools,
        deleted_tools,
    )

    return MCPBootstrapResult(
        created_servers=created_servers,
        created_tools=created_tools,
        updated_tools=updated_tools,
        deleted_tools=deleted_tools,
    )


def _find_default_server(
    servers: list[MCPServer],
    server_url: str,  # noqa: ARG001
) -> MCPServer | None:
    for server in servers:
        if server.name == DEFAULT_DOCUBRAIN_MCP_SERVER_NAME:
            return server
    return None


def _sync_tools_for_server(
    db_session: Session,
    server: MCPServer,
    discovered_tools: list[DiscoveredMCPTool],
) -> tuple[int, int, int]:
    existing_tools = get_tools_by_mcp_server_id(server.id, db_session)
    existing_by_name = {tool.name: tool for tool in existing_tools}
    processed_names: set[str] = set()
    created_tools = 0
    updated_tools = 0

    for discovered_tool in discovered_tools:
        tool_name = discovered_tool.name
        if not tool_name:
            continue

        processed_names.add(tool_name)
        description = discovered_tool.description or ""
        display_name = _display_name_for_tool(discovered_tool)
        input_schema = discovered_tool.inputSchema

        existing_tool = existing_by_name.get(tool_name)
        if existing_tool is None:
            new_tool = create_tool__no_commit(
                name=tool_name,
                description=description,
                openapi_schema=None,
                custom_headers=None,
                user_id=None,
                db_session=db_session,
                passthrough_auth=False,
                mcp_server_id=server.id,
                enabled=True,
            )
            new_tool.display_name = display_name
            new_tool.mcp_input_schema = input_schema
            created_tools += 1
            continue

        if _update_existing_tool(existing_tool, description, display_name, input_schema):
            updated_tools += 1

    deleted_tools = 0
    for name, existing_tool in existing_by_name.items():
        if name not in processed_names:
            delete_tool__no_commit(existing_tool.id, db_session)
            deleted_tools += 1

    return created_tools, updated_tools, deleted_tools


def _display_name_for_tool(tool: DiscoveredMCPTool) -> str:
    annotations = getattr(tool, "annotations", None)
    annotations_title = getattr(annotations, "title", None) if annotations else None
    return tool.title or annotations_title or tool.name


def _update_existing_tool(
    tool: Tool,
    description: str,
    display_name: str,
    input_schema: dict,
) -> bool:
    changed = False
    if tool.description != description:
        tool.description = description
        changed = True
    if tool.display_name != display_name:
        tool.display_name = display_name
        changed = True
    if tool.mcp_input_schema != input_schema:
        tool.mcp_input_schema = input_schema
        changed = True
    return changed


def _assign_mcp_tools_to_default_persona(
    db_session: Session,
    mcp_server: MCPServer,
) -> None:
    """Add all tools from *mcp_server* to the default persona's tool list.

    This makes the Google Workspace MCP tools available in normal chat sessions
    (not just the agent editor). The operation is idempotent — tools already
    present are not duplicated.
    """
    try:
        from docubrain.db.persona import get_default_assistant
        from docubrain.db.tools import get_tools_by_mcp_server_id as _get_tools

        persona = get_default_assistant(db_session)
        if persona is None:
            logger.warning(
                "Default assistant not found; skipping MCP tool assignment to default persona"
            )
            return

        mcp_tools = _get_tools(mcp_server.id, db_session)
        if not mcp_tools:
            return

        # Helpful debug information for diagnosing assignment failures
        logger.info(
            "Attempting MCP tool assignment: persona=%s(id=%s) mcp_server=%s(id=%s) mcp_tools=%s",
            persona.name,
            persona.id,
            mcp_server.name,
            mcp_server.id,
            len(mcp_tools),
        )
        logger.debug("MCP tool ids: %s", [t.id for t in mcp_tools])

        existing_tool_ids = {t.id for t in persona.tools}
        logger.debug("Existing persona tool ids: %s", existing_tool_ids)
        added = 0
        for tool in mcp_tools:
            if tool.id not in existing_tool_ids:
                persona.tools.append(tool)
                added += 1

        if added:
            db_session.commit()
            logger.info(
                "Assigned %d Google Workspace MCP tool(s) to default persona '%s'",
                added,
                persona.name,
            )
            return

        # No tools were appended via relationship append; attempt a robust fallback
        # that directly creates persona__tool association rows.
        try:
            from docubrain.db.models import Persona__Tool

            missing = [t for t in mcp_tools if t.id not in existing_tool_ids]
            if not missing:
                logger.debug(
                    "No missing MCP tools detected for persona '%s'; nothing to do",
                    persona.name,
                )
                return

            for tool in missing:
                assoc = Persona__Tool(persona_id=persona.id, tool_id=tool.id)
                db_session.add(assoc)
            db_session.commit()
            logger.info(
                "Fallback: created %d persona__tool rows for default persona '%s'",
                len(missing),
                persona.name,
            )
        except Exception:
            logger.exception(
                "Fallback failed when creating persona__tool rows; tools will still be available via the agent editor"
            )
    except Exception:
        logger.exception(
            "Failed to assign MCP tools to default persona — "
            "tools will still be available via the agent editor"
        )
