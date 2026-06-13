"""General / master operations: ``/v1/chain`` and ``/v1/device/reconnect``.

These are the ``00`` commands the library puts on ``Lucon`` — they physically
reach the master only (CONTEXT.md / ADR-0002). Identity reads are cached on
connect; destructive ops (network, factory-reset, restart, save+restart) require
``confirm`` equal to the device serial, mirroring the library's ``set_ip_checked``.
"""

from __future__ import annotations

from typing import Annotated, Any, Callable

from fastapi import APIRouter, Path, Query, status

from lucon import Lucon
from lucon_api._reads import collect
from lucon_api.deps import GatewayDepInstance, require_confirm
from lucon_api.errors import InvalidValueError, NotFoundError
from lucon_api.schemas import (
    ChainIdentity,
    ConfigIn,
    ConfigOut,
    FactoryResetIn,
    FieldRead,
    NetworkIn,
    ReconnectAck,
    RestartIn,
    SaveAndRestartIn,
    SaveIn,
    WriteAck,
    detail_value,
)

router = APIRouter(tags=["chain"])

# general field-name (granular URL segment / ChainIdentity key) -> reader.
CHAIN_READS: dict[str, Callable[[Lucon], Any]] = {
    "firmware": lambda lucon: lucon.firmware(),
    "serial": lambda lucon: lucon.serial(),
    "mac": lambda lucon: lucon.mac(),
    "ip": lambda lucon: lucon.ip(),
    "subnet": lambda lucon: lucon.subnet(),
    "udp_port": lambda lucon: lucon.udp_port(),
    "bootloader": lambda lucon: lucon.bootloader(),
    "pcb_revision_control": lambda lucon: lucon.pcb_revision_control(),
    "pcb_revision_power": lambda lucon: lucon.pcb_revision_power(),
    "supply_voltage_mv": lambda lucon: lucon.supply_voltage_mv(),
    "controller_offset": lambda lucon: lucon.controller_offset(),
    "error_buffer": lambda lucon: lucon.error_buffer(),
    "persisted": lambda lucon: lucon.is_persisted(),
}

_NET_WARNING = (
    "Network change written to Temporary memory. Persist with POST /v1/chain/save; "
    "it takes effect on restart and MAY change the device address and orphan this gateway."
)


@router.get("/chain", response_model=ChainIdentity)
def get_chain(
    gateway: GatewayDepInstance,
    detail: Annotated[str, Query(pattern="^(curated|full)$")] = "curated",
) -> ChainIdentity:
    """Master identity + chain topology (curated from cache, or ``?detail=full``)."""
    if detail == "full":
        fields = list(CHAIN_READS)
        readings, unavailable = gateway.with_chain(
            lambda lucon: collect(lucon, CHAIN_READS, fields)
        )
        return ChainIdentity(
            offsets=gateway.offsets(),
            online_channels=gateway.online_channels(),
            unavailable=unavailable,
            **readings,
        )

    persisted = gateway.with_chain(lambda lucon: lucon.is_persisted())
    return ChainIdentity(
        firmware=gateway.firmware,
        serial=gateway.serial,
        mac=gateway.mac,
        controller_offset=gateway.controller_offset,
        persisted=persisted,
        offsets=gateway.offsets(),
        online_channels=gateway.online_channels(),
    )


@router.put("/chain/network", response_model=WriteAck)
def set_network(gateway: GatewayDepInstance, body: NetworkIn) -> WriteAck:
    """Change the master's network identity (destructive; confirm = serial)."""
    require_confirm(gateway, body.confirm)
    if body.ip is None and body.subnet is None and body.controller_offset is None:
        raise InvalidValueError("provide at least one of ip, subnet, controller_offset")
    serial = body.confirm

    def write(lucon: Lucon) -> None:
        if body.ip is not None:
            # Always use the firmware serial-checked command for an IP change
            # (S00SIP), so the device's own identity guard applies even when
            # subnet/offset are changed in the same request.
            lucon.set_ip_checked(body.ip, serial)
        if body.subnet is not None:
            lucon.set_subnet(body.subnet)
        if body.controller_offset is not None:
            lucon.set_controller_offset(body.controller_offset)

    gateway.with_chain(write)
    return WriteAck(warning=_NET_WARNING)


@router.post("/chain/save", response_model=WriteAck)
def save_chain(gateway: GatewayDepInstance, body: SaveIn) -> WriteAck:
    """Promote Temporary to Permanent memory (scope 0/1/2)."""
    gateway.with_chain(lambda lucon: lucon.save(body.scope))
    return WriteAck()


@router.post("/chain/factory-reset", response_model=WriteAck)
def factory_reset(gateway: GatewayDepInstance, body: FactoryResetIn) -> WriteAck:
    """Factory-reset to defaults (destructive; confirm = serial)."""
    require_confirm(gateway, body.confirm)
    gateway.with_chain(lambda lucon: lucon.factory_reset(body.scope))
    return WriteAck(
        warning="Factory reset applied. Persist with POST /v1/chain/save if intended."
    )


@router.post("/chain/restart", response_model=WriteAck)
def restart(gateway: GatewayDepInstance, body: RestartIn) -> WriteAck:
    """Restart the chain (destructive; confirm = serial). The gateway reconnects."""
    require_confirm(gateway, body.confirm)
    gateway.with_chain(lambda lucon: lucon.restart())
    gateway.note_disruption()
    return WriteAck(
        warning="Restart issued; the device will reboot and the gateway will reconnect. Watch /readyz."
    )


@router.post("/chain/save-and-restart", response_model=WriteAck)
def save_and_restart(gateway: GatewayDepInstance, body: SaveAndRestartIn) -> WriteAck:
    """Save (per scope) then restart (destructive; confirm = serial)."""
    require_confirm(gateway, body.confirm)
    gateway.with_chain(lambda lucon: lucon.save_and_restart(body.scope))
    gateway.note_disruption()
    return WriteAck(
        warning="Save+restart issued; the device will reboot and the gateway will reconnect. Watch /readyz."
    )


@router.get("/chain/config", response_model=ConfigOut)
def get_config(gateway: GatewayDepInstance) -> ConfigOut:
    """Read the runtime sub-45 mA interpretation flag (no device I/O)."""
    return ConfigOut(current_tenths=gateway.current_tenths)


@router.patch("/chain/config", response_model=ConfigOut)
def patch_config(gateway: GatewayDepInstance, body: ConfigIn) -> ConfigOut:
    """Toggle current_tenths at runtime (read-only reinterpretation; no device I/O)."""
    gateway.set_current_tenths(body.current_tenths)
    return ConfigOut(current_tenths=gateway.current_tenths)


@router.post(
    "/device/reconnect",
    response_model=ReconnectAck,
    status_code=status.HTTP_202_ACCEPTED,
)
def reconnect(gateway: GatewayDepInstance) -> ReconnectAck:
    """Force a fresh open()+tree-rebuild (e.g. after manual device changes)."""
    gateway.request_reconnect()
    return ReconnectAck(requested=True, ready=gateway.ready)


# Registered last so the literal GET routes above (/chain, /chain/config) win.
@router.get("/chain/{field}", response_model=FieldRead)
def read_chain_field(
    gateway: GatewayDepInstance,
    field: Annotated[str, Path()],
) -> FieldRead:
    """Read exactly one general value (one device round-trip)."""
    reader = CHAIN_READS.get(field)
    if reader is None:
        raise NotFoundError(f"unknown chain field {field!r}")
    value = gateway.with_chain(reader)
    return FieldRead(field=field, value=detail_value(value))
