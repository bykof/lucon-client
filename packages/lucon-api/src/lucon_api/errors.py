"""HTTP error model: one JSON envelope, and handlers mapping every failure.

The mapping (grilled & recorded): client/validation -> 422, unknown channel or
offset -> 404, device unreachable -> 503, device timeout -> 504, device ``:E``
rejection or garbled datagram -> 502. A device ``:E`` text is firmware/locale-
dependent, so we never parse it — we pass the verbatim message and raw bytes
through to the caller.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from lucon import (
    LuconCommandError,
    LuconConnectionError,
    LuconError,
    LuconProtocolError,
    LuconTimeoutError,
)

_LOG = logging.getLogger("lucon_api")


class APIError(Exception):
    """Base for gateway-originated HTTP errors. Subclasses set status + code."""

    status_code: int = 500
    code: str = "error"

    def __init__(
        self,
        message: str,
        *,
        headers: dict[str, str] | None = None,
        **details: Any,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.headers = headers or {}
        self.details = details


class NotFoundError(APIError):
    """Addressed a channel or controller that is not in the discovered tree."""

    status_code = 404
    code = "not_found"


class DeviceUnavailableError(APIError):
    """The gateway has no live connection to the device right now."""

    status_code = 503
    code = "device_unavailable"


class BusyError(APIError):
    """The device-command queue is full; the client should back off and retry."""

    status_code = 503
    code = "busy"


class DeadlineError(APIError):
    """The request waited past its deadline for the serialized device transport."""

    status_code = 504
    code = "deadline_exceeded"


class ConfirmationError(APIError):
    """A destructive operation was attempted without the required confirmation."""

    status_code = 422
    code = "confirmation_required"


class InvalidValueError(APIError):
    """A client supplied a value the schema couldn't catch (e.g. a cross-field rule)."""

    status_code = 422
    code = "invalid_value"


class UnauthorizedError(APIError):
    """A valid API key is required and was missing or wrong."""

    status_code = 401
    code = "unauthorized"


def _raw(data: bytes | None) -> str | None:
    """Render raw device bytes for an error body (ASCII, replacement on junk)."""
    if data is None:
        return None
    return data.decode("ascii", errors="replace")


def _envelope(code: str, message: str, **details: Any) -> dict[str, Any]:
    """Build the single error envelope, dropping ``None`` details."""
    error: dict[str, Any] = {"type": code, "message": message}
    error.update({k: v for k, v in details.items() if v is not None})
    return {"error": error}


def register_exception_handlers(app: FastAPI) -> None:
    """Install the gateway's exception handlers on ``app``.

    Starlette resolves handlers by exception MRO (most specific wins), so the
    ``LuconCommandError``/``LuconProtocolError`` handlers take precedence over
    the ``LuconError`` catch-all regardless of registration order.
    """

    @app.exception_handler(APIError)
    async def _api(_: Request, exc: APIError) -> JSONResponse:
        headers = dict(exc.headers)
        # Any 503 (unavailable/busy) is transient — tell clients to back off.
        if exc.status_code == 503:
            headers.setdefault("Retry-After", "1")
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.code, exc.message, **exc.details),
            headers=headers,
        )

    @app.exception_handler(LuconCommandError)
    async def _cmd(_: Request, exc: LuconCommandError) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content=_envelope(
                "device_rejected",
                "the device rejected the command",
                device_message=exc.message,
                command=_raw(exc.command),
                raw=_raw(exc.raw),
            ),
        )

    @app.exception_handler(LuconTimeoutError)
    async def _timeout(_: Request, exc: LuconTimeoutError) -> JSONResponse:
        return JSONResponse(
            status_code=504,
            content=_envelope("device_timeout", str(exc)),
        )

    @app.exception_handler(LuconConnectionError)
    async def _conn(_: Request, exc: LuconConnectionError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content=_envelope("device_unavailable", str(exc)),
            headers={"Retry-After": "1"},
        )

    @app.exception_handler(LuconProtocolError)
    async def _proto(_: Request, exc: LuconProtocolError) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content=_envelope("device_protocol_error", str(exc), raw=_raw(exc.raw)),
        )

    @app.exception_handler(LuconError)
    async def _lucon(_: Request, exc: LuconError) -> JSONResponse:
        # Base catch-all: the meaningful subclasses are handled above and the
        # addressing errors are translated to NotFoundError in the gateway, so
        # reaching here is unexpected.
        return JSONResponse(
            status_code=500,
            content=_envelope("device_error", str(exc)),
        )

    @app.exception_handler(ValueError)
    async def _value(_: Request, exc: ValueError) -> JSONResponse:
        # Client-side value errors are raised explicitly as InvalidValueError
        # (422); pydantic catches the rest of the client bounds. So a bare
        # ValueError reaching here is device-origin — a numeric read failing to
        # parse a malformed device reply — or an internal bug. Map to 502 and
        # log, rather than mislabeling the client (422).
        _LOG.warning("unhandled ValueError mapped to 502: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=502,
            content=_envelope("device_protocol_error", f"could not parse a device value: {exc}"),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_envelope(
                "validation_error",
                "request validation failed",
                errors=jsonable_encoder(exc.errors()),
            ),
        )
