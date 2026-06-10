"""Per-channel operations: ``/v1/channels`` (the full per-channel command set).

Reads are one device round-trip each through the serialized transport, so the
surface is cost-shaped (grilled): ``GET /channels`` is summary rows, ``GET
/channels/{n}`` is a curated set by default (``?detail=full`` for everything),
and ``GET /channels/{n}/{field}`` fetches exactly one value. Composite reads are
best-effort: a value the device rejects for the current mode is listed under
``unavailable`` rather than failing the whole response (connection/timeout
errors still propagate).
"""

from __future__ import annotations

from typing import Annotated, Any, Callable, Literal

from fastapi import APIRouter, Depends, Path, Query

from lucon import Channel, LuconCommandError, Mode
from lucon_api._reads import collect
from lucon_api.api_enums import (
    edge_from_name,
    edge_name,
    mode_name,
    output_polarity_from_name,
    output_polarity_name,
    output_type_from_name,
    output_type_name,
    source_from_name,
    source_name,
)
from lucon_api.deps import get_gateway
from lucon_api.errors import NotFoundError
from lucon_api.gateway import Gateway
from lucon_api.schemas import (
    ChannelDetail,
    ChannelReadings,
    ChannelSummary,
    ContinuousModeIn,
    FieldRead,
    LimitsIn,
    LimitsOut,
    ModeIn,
    ModeOut,
    NoneModeIn,
    PulseModeIn,
    SwitchModeIn,
    TriggerInputIn,
    TriggerInputOut,
    TriggerOutputIn,
    TriggerOutputOut,
    WriteAck,
    detail_value,
)

router = APIRouter(tags=["channels"])

GatewayDep = Annotated[Gateway, Depends(get_gateway)]
ChannelNum = Annotated[int, Path(ge=1, le=96)]

# field-name (also the granular URL segment and ChannelReadings key) -> reader.
CHANNEL_READS: dict[str, Callable[[Channel], Any]] = {
    "temperature_c": lambda ch: ch.temperature(),
    "mode": lambda ch: mode_name(ch.mode()),
    "pulse_current_ma": lambda ch: ch.pulse_current(),
    "switch_current_ma": lambda ch: ch.switch_current(),
    "current_flow_ma": lambda ch: ch.current_flow(),
    "continuous_limit_ma": lambda ch: ch.continuous_limit(),
    "pulse_limit_ma": lambda ch: ch.pulse_limit(),
    "voltage_limit_mv": lambda ch: ch.voltage_limit(),
    "pulse_width_us": lambda ch: ch.pulse_width(),
    "pulse_delay_us": lambda ch: ch.pulse_delay(),
    "cooling_time": lambda ch: ch.cooling_time(),
    "led_voltage_mv": lambda ch: ch.led_voltage(),
    "led_voltage_in_mv": lambda ch: ch.led_voltage_in(),
    "led_voltage_out_mv": lambda ch: ch.led_voltage_out(),
    "last_pulse_voltage_mv": lambda ch: ch.last_pulse_voltage(),
    "last_pulse_current_ma": lambda ch: ch.last_pulse_current(),
    "pulse_input_polarity": lambda ch: edge_name(ch.pulse_input_polarity()),
    "switch_input_active_high": lambda ch: ch.switch_input_polarity(),
    "output_enabled": lambda ch: ch.output_enabled(),
    "output_polarity": lambda ch: output_polarity_name(ch.output_polarity()),
    "output_source": lambda ch: source_name(ch.output_source()),
    "output_type": lambda ch: output_type_name(ch.output_type()),
    "output_delay_us": lambda ch: ch.output_delay(),
    "output_length_us": lambda ch: ch.output_length(),
    "persisted": lambda ch: ch.is_persisted(),
}

CURATED_FIELDS = ["mode", "temperature_c", "current_flow_ma", "led_voltage_mv", "persisted"]


def _collect(channel: Channel, fields: list[str]) -> tuple[dict[str, Any], list[str]]:
    """Best-effort read of ``fields`` (see :func:`lucon_api._reads.collect`)."""
    return collect(channel, CHANNEL_READS, fields)


def _provided(model: LimitsIn | TriggerInputIn | TriggerOutputIn) -> dict[str, Any]:
    """Fields the client actually sent and that are non-null (partial PUT)."""
    return {k: v for k, v in model.model_dump(exclude_unset=True).items() if v is not None}


@router.get("/channels", response_model=list[ChannelSummary])
def list_channels(gateway: GatewayDep) -> list[ChannelSummary]:
    """Summary rows for every online channel (mode + temperature + persisted)."""
    rows: list[ChannelSummary] = []
    for num in gateway.online_channels():
        readings, unavailable = gateway.with_channel(
            num, lambda ch: _collect(ch, ["mode", "temperature_c", "persisted"])
        )
        offset = (num - 1) // 4
        rows.append(
            ChannelSummary(
                channel_num=num,
                offset=offset,
                local_index=num - offset * 4,
                mode=readings.get("mode"),
                temperature_c=readings.get("temperature_c"),
                persisted=readings.get("persisted"),
                unavailable=unavailable,
            )
        )
    return rows


@router.get("/channels/{n}", response_model=ChannelDetail)
def get_channel(
    gateway: GatewayDep,
    n: ChannelNum,
    detail: Annotated[Literal["curated", "full"], Query()] = "curated",
) -> ChannelDetail:
    """Channel identity + readings (curated by default, ``?detail=full`` for all)."""
    fields = list(CHANNEL_READS) if detail == "full" else CURATED_FIELDS

    def read(channel: Channel) -> tuple[int, int, int, dict[str, Any], list[str]]:
        readings, unavailable = _collect(channel, fields)
        return channel.channel_num, channel.controller.offset, channel.local_index, readings, unavailable

    num, offset, local, readings, unavailable = gateway.with_channel(n, read)
    return ChannelDetail(
        channel_num=num,
        offset=offset,
        local_index=local,
        readings=ChannelReadings(**readings),
        unavailable=unavailable,
    )


@router.get("/channels/{n}/mode", response_model=ModeOut)
def get_mode(gateway: GatewayDep, n: ChannelNum) -> ModeOut:
    """The channel's mode, enriched with that mode's parameters when readable."""

    def read(channel: Channel) -> ModeOut:
        mode = channel.mode()
        out = ModeOut(mode=mode_name(mode))
        try:
            if mode is Mode.CONTINUOUS:
                out.ma = channel.current_flow()
            elif mode is Mode.SWITCH:
                out.ma = channel.switch_current()
            elif mode is Mode.PULSE:
                out.ma = channel.pulse_current()
                out.delay_us = channel.pulse_delay()
                out.duration_us = channel.pulse_width()
        except LuconCommandError:
            pass  # parameters not available in this state; mode alone is enough
        return out

    return gateway.with_channel(n, read)


@router.put("/channels/{n}/mode", response_model=WriteAck)
def set_mode(gateway: GatewayDep, n: ChannelNum, body: ModeIn) -> WriteAck:
    """Select the operating mode (writes Temporary memory; POST /save to persist)."""
    spec = body.root

    def write(channel: Channel) -> None:
        if isinstance(spec, ContinuousModeIn):
            channel.set_continuous(spec.ma)
        elif isinstance(spec, SwitchModeIn):
            channel.set_switch_current(spec.ma)
        elif isinstance(spec, PulseModeIn):
            channel.set_pulse(spec.ma, spec.delay_us, spec.duration_us)
        elif isinstance(spec, NoneModeIn):
            channel.set_none()

    gateway.with_channel(n, write)
    return WriteAck()


@router.get("/channels/{n}/limits", response_model=LimitsOut)
def get_limits(gateway: GatewayDep, n: ChannelNum) -> LimitsOut:
    """The channel's three protective limits."""

    def read(channel: Channel) -> LimitsOut:
        return LimitsOut(
            continuous_ma=channel.continuous_limit(),
            pulse_ma=channel.pulse_limit(),
            voltage_mv=channel.voltage_limit(),
        )

    return gateway.with_channel(n, read)


@router.put("/channels/{n}/limits", response_model=WriteAck)
def set_limits(gateway: GatewayDep, n: ChannelNum, body: LimitsIn) -> WriteAck:
    """Set any of the protective limits (only the fields you send are written)."""
    vals = _provided(body)

    def write(channel: Channel) -> None:
        if "continuous_ma" in vals:
            channel.set_continuous_limit(float(vals["continuous_ma"]))
        if "pulse_ma" in vals:
            channel.set_pulse_limit(float(vals["pulse_ma"]))
        if "voltage_mv" in vals:
            channel.set_voltage_limit(int(vals["voltage_mv"]))

    gateway.with_channel(n, write)
    return WriteAck()


@router.get("/channels/{n}/trigger/input", response_model=TriggerInputOut)
def get_trigger_input(gateway: GatewayDep, n: ChannelNum) -> TriggerInputOut:
    """Pulse-input edge, switch input polarity, and stored switch current."""

    def read(channel: Channel) -> TriggerInputOut:
        return TriggerInputOut(
            pulse_edge=edge_name(channel.pulse_input_polarity()),
            switch_active_high=channel.switch_input_polarity(),
            switch_current_ma=channel.switch_current(),
        )

    return gateway.with_channel(n, read)


@router.put("/channels/{n}/trigger/input", response_model=WriteAck)
def set_trigger_input(gateway: GatewayDep, n: ChannelNum, body: TriggerInputIn) -> WriteAck:
    """Configure the trigger/switch input (only the fields you send are written)."""
    vals = _provided(body)

    def write(channel: Channel) -> None:
        if "pulse_edge" in vals:
            channel.set_pulse_input_polarity(edge_from_name(str(vals["pulse_edge"])))
        if "switch_active_high" in vals:
            channel.set_switch_input_polarity(bool(vals["switch_active_high"]))
        if "switch_current_ma" in vals:
            channel.set_switch_current_value(float(vals["switch_current_ma"]))

    gateway.with_channel(n, write)
    return WriteAck()


@router.get("/channels/{n}/trigger/output", response_model=TriggerOutputOut)
def get_trigger_output(gateway: GatewayDep, n: ChannelNum) -> TriggerOutputOut:
    """Full trigger-output configuration read-back."""

    def read(channel: Channel) -> TriggerOutputOut:
        return TriggerOutputOut(
            enabled=channel.output_enabled(),
            polarity=output_polarity_name(channel.output_polarity()),
            source=source_name(channel.output_source()),
            type=output_type_name(channel.output_type()),
            delay_us=channel.output_delay(),
            length_us=channel.output_length(),
        )

    return gateway.with_channel(n, read)


@router.put("/channels/{n}/trigger/output", response_model=WriteAck)
def set_trigger_output(gateway: GatewayDep, n: ChannelNum, body: TriggerOutputIn) -> WriteAck:
    """Configure the trigger output (only the fields you send are written)."""
    vals = _provided(body)

    def write(channel: Channel) -> None:
        if "enabled" in vals:
            channel.set_output_enabled(bool(vals["enabled"]))
        if "polarity" in vals:
            channel.set_output_polarity(output_polarity_from_name(str(vals["polarity"])))
        if "source" in vals:
            channel.set_output_source(source_from_name(str(vals["source"])))
        if "type" in vals:
            channel.set_output_type(output_type_from_name(str(vals["type"])))
        if "delay_us" in vals:
            channel.set_output_delay(int(vals["delay_us"]))
        if "length_us" in vals:
            channel.set_output_length(int(vals["length_us"]))

    gateway.with_channel(n, write)
    return WriteAck()


@router.post("/channels/{n}/save", response_model=WriteAck)
def save_channel(gateway: GatewayDep, n: ChannelNum) -> WriteAck:
    """Promote this channel's Temporary memory to Permanent memory."""
    gateway.with_channel(n, lambda ch: ch.save())
    return WriteAck()


@router.post("/channels/{n}/reset", response_model=WriteAck)
def reset_channel(gateway: GatewayDep, n: ChannelNum) -> WriteAck:
    """Reset this channel to its defaults (Temporary memory)."""
    gateway.with_channel(n, lambda ch: ch.reset())
    return WriteAck()


# Registered LAST so the literal-suffix routes above (mode, limits, …) win.
@router.get("/channels/{n}/{field}", response_model=FieldRead)
def read_field(gateway: GatewayDep, n: ChannelNum, field: str) -> FieldRead:
    """Read exactly one value (one device round-trip)."""
    reader = CHANNEL_READS.get(field)
    if reader is None:
        raise NotFoundError(f"unknown channel field {field!r}")
    value = gateway.with_channel(n, reader)
    return FieldRead(field=field, value=detail_value(value))
