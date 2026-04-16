import functools
import importlib
import inspect
from typing import Any
from typing import TypeVar

from docubrain.configs.app_configs import API_SERVER_HOST
from docubrain.configs.app_configs import API_SERVER_PROTOCOL
from docubrain.configs.app_configs import API_SERVER_URL_OVERRIDE_FOR_HTTP_REQUESTS
from docubrain.configs.app_configs import APP_API_PREFIX
from docubrain.configs.app_configs import APP_PORT
from docubrain.configs.app_configs import DEV_MODE
from docubrain.utils.logger import setup_logger

logger = setup_logger()


class DocubrainVersion:
    def __init__(self) -> None:
        self._is_ee = False

    def set_ee(self) -> None:
        self._is_ee = True

    def unset_ee(self) -> None:
        self._is_ee = False

    def is_ee_version(self) -> bool:
        return self._is_ee


global_version = DocubrainVersion()

def set_is_ee_based_on_env_variable() -> None:
    """Always run in core-only mode."""
    if global_version.is_ee_version():
        global_version.unset_ee()


@functools.lru_cache(maxsize=128)
def fetch_versioned_implementation(module: str, attribute: str) -> Any:
    """Fetch implementation from the core module namespace."""
    logger.debug("Fetching versioned implementation for %s.%s", module, attribute)
    return getattr(importlib.import_module(module), attribute)


T = TypeVar("T")


def fetch_versioned_implementation_with_fallback(
    module: str, attribute: str, fallback: T
) -> T:
    """
    Attempts to fetch a versioned implementation of a specified attribute from a given module.
    If the attempt fails (e.g., due to an import error or missing attribute), the function logs
    a warning and returns the provided fallback implementation.

    Args:
        module (str): The name of the module from which to fetch the attribute.
        attribute (str): The name of the attribute to fetch from the module.
        fallback (T): The fallback implementation to return if fetching the attribute fails.

    Returns:
        T: The fetched implementation if successful, otherwise the provided fallback.
    """
    try:
        return fetch_versioned_implementation(module, attribute)
    except Exception:
        return fallback


def noop_fallback(*args: Any, **kwargs: Any) -> None:
    """
    A no-op (no operation) fallback function that accepts any arguments but does nothing.
    This is often used as a default or placeholder callback function.

    Args:
        *args (Any): Positional arguments, which are ignored.
        **kwargs (Any): Keyword arguments, which are ignored.

    Returns:
        None
    """


def fetch_ee_implementation_or_noop(
    module: str, attribute: str, noop_return_value: Any = None
) -> Any:
    """
    Fetches an EE implementation if EE is enabled, otherwise returns a no-op function.
    Raises an exception if EE is enabled but the fetch fails.

    Args:
        module (str): The name of the module from which to fetch the attribute.
        attribute (str): The name of the attribute to fetch from the module.

    Returns:
        Any: The fetched EE implementation if successful and EE is enabled, otherwise a no-op function.

    Raises:
        Exception: If EE is enabled but the fetch fails.
    """
    if not global_version.is_ee_version():
        if inspect.iscoroutinefunction(noop_return_value):

            async def async_noop(*args: Any, **kwargs: Any) -> Any:
                return await noop_return_value(*args, **kwargs)

            return async_noop

        else:

            def sync_noop(*args: Any, **kwargs: Any) -> Any:  # noqa: ARG001
                return noop_return_value

            return sync_noop
    try:
        return fetch_versioned_implementation(module, attribute)
    except Exception as e:
        logger.error(f"Failed to fetch implementation for {module}.{attribute}: {e}")
        raise


def build_api_server_url_for_http_requests(
    respect_env_override_if_set: bool = False,
) -> str:
    """
    Builds the API server URL for HTTP requests.
    """
    if DEV_MODE:
        url = f"http://127.0.0.1:{APP_PORT}"
    elif respect_env_override_if_set and API_SERVER_URL_OVERRIDE_FOR_HTTP_REQUESTS:
        url = API_SERVER_URL_OVERRIDE_FOR_HTTP_REQUESTS.rstrip("/")
    else:
        url = f"{API_SERVER_PROTOCOL}://{API_SERVER_HOST}:{APP_PORT}"

    if APP_API_PREFIX:
        url += f"/{APP_API_PREFIX.strip('/')}"

    return url
