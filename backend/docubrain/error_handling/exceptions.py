"""DocubrainError — the single exception type for all DocuBrain business errors.

Raise ``DocubrainError`` instead of ``HTTPException`` in business code.  A global
FastAPI exception handler (registered via ``register_docubrain_exception_handlers``)
converts it into a JSON response with the standard
``{"error_code": "...", "detail": "..."}`` shape.

Usage::

    from docubrain.error_handling.error_codes import DocubrainErrorCode
    from docubrain.error_handling.exceptions import DocubrainError

    raise DocubrainError(DocubrainErrorCode.NOT_FOUND, "Session not found")

For upstream errors with a dynamic HTTP status (e.g. billing service),
use ``status_code_override``::

    raise DocubrainError(
        DocubrainErrorCode.BAD_GATEWAY,
        detail,
        status_code_override=upstream_status,
    )
"""

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import JSONResponse

from docubrain.error_handling.error_codes import DocubrainErrorCode
from docubrain.utils.logger import setup_logger

logger = setup_logger()


class DocubrainError(Exception):
    """Structured error that maps to a specific ``DocubrainErrorCode``.

    Attributes:
        error_code: The ``DocubrainErrorCode`` enum member.
        detail: Human-readable detail (defaults to the error code string).
        status_code: HTTP status — either overridden or from the error code.
    """

    def __init__(
        self,
        error_code: DocubrainErrorCode,
        detail: str | None = None,
        *,
        status_code_override: int | None = None,
    ) -> None:
        resolved_detail = detail or error_code.code
        super().__init__(resolved_detail)
        self.error_code = error_code
        self.detail = resolved_detail
        self._status_code_override = status_code_override

    @property
    def status_code(self) -> int:
        return self._status_code_override or self.error_code.status_code


def log_docubrain_error(exc: DocubrainError) -> None:
    detail = exc.detail
    status_code = exc.status_code
    if status_code >= 500:
        logger.error(f"DocubrainError {exc.error_code.code}: {detail}")
    elif status_code >= 400:
        logger.warning(f"DocubrainError {exc.error_code.code}: {detail}")


def docubrain_error_to_json_response(exc: DocubrainError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.error_code.detail(exc.detail),
    )


def register_docubrain_exception_handlers(app: FastAPI) -> None:
    """Register a global handler that converts ``DocubrainError`` to JSON responses.

    Must be called *after* the app is created but *before* it starts serving.
    The handler logs at WARNING for 4xx and ERROR for 5xx.
    """

    @app.exception_handler(DocubrainError)
    async def _handle_docubrain_error(
        request: Request,  # noqa: ARG001
        exc: DocubrainError,
    ) -> JSONResponse:
        log_docubrain_error(exc)
        return docubrain_error_to_json_response(exc)
