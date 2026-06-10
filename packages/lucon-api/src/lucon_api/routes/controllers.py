"""Read-only chain topology: ``/v1/controllers`` (built on connect, no device I/O)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from lucon_api.deps import get_gateway
from lucon_api.gateway import Gateway
from lucon_api.schemas import ControllerOut

router = APIRouter(tags=["controllers"])

GatewayDep = Annotated[Gateway, Depends(get_gateway)]


@router.get("/controllers", response_model=list[ControllerOut])
def list_controllers(gateway: GatewayDep) -> list[ControllerOut]:
    """Every discovered controller and its global channel numbers."""
    return [ControllerOut(**c) for c in gateway.topology()]


@router.get("/controllers/{offset}", response_model=ControllerOut)
def get_controller(
    gateway: GatewayDep,
    offset: Annotated[int, Path(ge=0, le=23)],
) -> ControllerOut:
    """One controller by offset (404 if absent from the discovered chain)."""
    return ControllerOut(**gateway.controller_view(offset))
