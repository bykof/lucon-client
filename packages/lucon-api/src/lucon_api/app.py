"""FastAPI application factory: wires the gateway, routers, and error handlers.

Health/status routes are unversioned; everything else lives under ``/v1`` behind
the optional API-key guard. The single :class:`Gateway` and :class:`EventHub` are
created here and supervised across the app lifespan (ADR-0003/0004).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import APIRouter, Depends, FastAPI

from lucon_api._version import __version__
from lucon_api.config import Settings
from lucon_api.deps import api_key_guard
from lucon_api.errors import register_exception_handlers
from lucon_api.events import EventHub
from lucon_api.gateway import Gateway
from lucon_api.routes import chain, channels, controllers, health, raw
from lucon_api.routes import events as events_routes


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the gateway app. ``settings`` defaults to env-sourced :class:`Settings`."""
    settings = settings or Settings()  # host (and the rest) sourced from LUCON_ env
    hub = EventHub(settings.event_buffer)
    gateway = Gateway(settings, hub)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        hub.bind_loop(asyncio.get_running_loop())
        gateway.start()
        try:
            yield
        finally:
            gateway.stop()

    app = FastAPI(
        title="lucon-api",
        version=__version__,
        summary="REST gateway for the GEFASOFT LUCON 4C-20A-V LED controller.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.gateway = gateway
    app.state.hub = hub

    register_exception_handlers(app)

    # Unversioned liveness/readiness/status.
    app.include_router(health.router)

    # Versioned device surface, behind the (optional) API-key guard.
    v1 = APIRouter(prefix="/v1", dependencies=[Depends(api_key_guard)])
    v1.include_router(chain.router)
    v1.include_router(controllers.router)
    v1.include_router(channels.router)
    v1.include_router(events_routes.router)
    v1.include_router(raw.router)
    app.include_router(v1)

    return app
