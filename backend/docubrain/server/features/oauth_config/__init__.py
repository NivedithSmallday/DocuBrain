"""OAuth configuration feature module."""

from docubrain.server.features.oauth_config.api import admin_router
from docubrain.server.features.oauth_config.api import router

__all__ = ["admin_router", "router"]
