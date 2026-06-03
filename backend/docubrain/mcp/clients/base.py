from typing import Any
from typing import Protocol

from docubrain.mcp.schemas import ProviderCapability


class BaseMCPClient(Protocol):
    async def execute_tool(
        self,
        capability: ProviderCapability,
        arguments: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Execute a provider capability and return provider-native result records."""

