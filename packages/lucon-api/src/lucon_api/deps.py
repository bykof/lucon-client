"""FastAPI dependencies and small guards shared across routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from lucon_api.config import Settings
from lucon_api.errors import ConfirmationError, DeviceUnavailableError, UnauthorizedError
from lucon_api.events import EventHub
from lucon_api.gateway import Gateway


def get_settings(request: Request) -> Settings:
    """The process-wide :class:`Settings`."""
    settings: Settings = request.app.state.settings
    return settings


def get_gateway(request: Request) -> Gateway:
    """The shared :class:`Gateway`."""
    gateway: Gateway = request.app.state.gateway
    return gateway


def get_hub(request: Request) -> EventHub:
    """The shared :class:`EventHub`."""
    hub: EventHub = request.app.state.hub
    return hub


def api_key_guard(request: Request) -> None:
    """Router-level guard: enforce ``LUCON_API_KEY`` when configured.

    Open when no key is set (trusted-network default). Accepts the key via
    ``X-API-Key`` or ``Authorization: Bearer <key>``.
    """
    settings: Settings = request.app.state.settings
    if not settings.api_key:
        return
    provided = request.headers.get("x-api-key")
    if provided is None:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            provided = auth[len("bearer ") :].strip()
    if provided != settings.api_key:
        raise UnauthorizedError(
            "missing or invalid API key", headers={"WWW-Authenticate": "Bearer"}
        )


def require_confirm(gateway: Gateway, confirm: str) -> None:
    """Guard a destructive op: ``confirm`` must equal the device serial.

    Mirrors the library's ``set_ip_checked`` instinct — make the operator name
    the device before doing something hard to undo.
    """
    serial = gateway.serial
    if serial is None:
        raise DeviceUnavailableError("device serial unknown; cannot confirm a destructive op")
    if confirm != serial:
        raise ConfirmationError(
            "confirmation must equal the device serial (see GET /v1/chain/serial)"
        )


# Shared FastAPI dependency aliases (use in route signatures).
GatewayDepInstance = Annotated[Gateway, Depends(get_gateway)]
HubDep = Annotated[EventHub, Depends(get_hub)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
