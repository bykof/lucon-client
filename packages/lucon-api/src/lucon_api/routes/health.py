"""Service status and health probes (unversioned: ``/``, ``/healthz``, ``/readyz``)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from lucon_api._version import __version__
from lucon_api.deps import get_gateway
from lucon_api.errors import DeviceUnavailableError
from lucon_api.gateway import Gateway
from lucon_api.schemas import RootStatus

router = APIRouter(tags=["status"])

GatewayDep = Annotated[Gateway, Depends(get_gateway)]


@router.get("/", response_model=RootStatus)
def root(gateway: GatewayDep) -> RootStatus:
    """Service + chain status from cached identity (no device I/O)."""
    ready = gateway.ready
    try:
        offsets = gateway.offsets() if ready else []
        online = gateway.online_channels() if ready else []
    except DeviceUnavailableError:
        offsets, online = [], []
    return RootStatus(
        version=__version__,
        ready=ready,
        host=gateway.host,
        port=gateway.port,
        serial=gateway.serial,
        firmware=gateway.firmware,
        controller_offset=gateway.controller_offset,
        offsets=offsets,
        online_channels=online,
    )


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process is up (always 200)."""
    return {"status": "ok"}


@router.get("/readyz")
def readyz(response: Response, gateway: GatewayDep) -> dict[str, bool]:
    """Readiness: 200 only when a live device connection exists, else 503."""
    if not gateway.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"ready": False}
    return {"ready": True}
