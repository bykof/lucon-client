"""Pydantic request/response models — the typed HTTP surface.

Units stay explicit in field names (``_ma`` float, ``_mv`` int, ``_us`` int),
mirroring the library and CONTEXT.md so nothing is ambiguous on the wire. The
operating mode is a discriminated union: a channel is in exactly one mode, and
each carries its own current/timing with per-variant bounds.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, RootModel

from lucon_api.api_enums import (
    EdgeName,
    ModeName,
    OutputPolarityName,
    OutputTypeName,
    SourceName,
)

# Client-side bounds, identical to the library's (Channel/codec).
_MAX_CONTINUOUS_MA = 3000.0
_MAX_HIGH_MA = 20000.0
_MIN_VOLTAGE_MV = 1000
_MAX_VOLTAGE_MV = 60000
_MIN_PULSE_TIME_US = 5
_MAX_PULSE_TIME_US = 59_000_000
_MIN_PULSE_DELAY_US = 3
_MAX_PULSE_DELAY_US = 59_000_000
_MIN_OUTPUT_LENGTH_US = 20
_MAX_OUTPUT_LENGTH_US = 1_000_000
_MIN_OUTPUT_DELAY_US = 0
_MAX_OUTPUT_DELAY_US = 1_000_000


# --- mode (discriminated; writes Temporary memory) ----------------------


class ContinuousModeIn(BaseModel):
    """Continuous mode: drive ``ma`` mA until stopped (max 3 A)."""

    mode: Literal["continuous"]
    ma: float = Field(..., ge=0, le=_MAX_CONTINUOUS_MA)


class SwitchModeIn(BaseModel):
    """Switch mode: on while the trigger is active (max 20 A)."""

    mode: Literal["switch"]
    ma: float = Field(..., ge=0, le=_MAX_HIGH_MA)


class PulseModeIn(BaseModel):
    """Pulse mode: one ``duration_us`` pulse per trigger after ``delay_us`` (max 20 A)."""

    mode: Literal["pulse"]
    ma: float = Field(..., ge=0, le=_MAX_HIGH_MA)
    delay_us: int = Field(..., ge=_MIN_PULSE_DELAY_US, le=_MAX_PULSE_DELAY_US)
    duration_us: int = Field(..., ge=_MIN_PULSE_TIME_US, le=_MAX_PULSE_TIME_US)


class NoneModeIn(BaseModel):
    """None / idle mode: disable output and trigger evaluation."""

    mode: Literal["none"]


class ModeIn(
    RootModel[
        Annotated[
            Union[ContinuousModeIn, SwitchModeIn, PulseModeIn, NoneModeIn],
            Field(discriminator="mode"),
        ]
    ]
):
    """Discriminated body for ``PUT /channels/{n}/mode``."""


class ModeOut(BaseModel):
    """Mode read-back, enriched with the active mode's parameters when known."""

    mode: ModeName
    ma: float | None = None
    delay_us: int | None = None
    duration_us: int | None = None


# --- limits & trigger config (partial PUT: only provided fields applied) -


class LimitsIn(BaseModel):
    """Per-channel protective limits. Only the fields you send are written.

    Note: limit currents (`L`/`LP`) have whole-mA resolution on the device — a
    fraction is *truncated* (CONTEXT.md, confirmed fw 0.5.0), so `10.9` stores as
    `10`. Pass whole numbers to avoid surprise.
    """

    continuous_ma: float | None = Field(
        None, ge=0, le=_MAX_CONTINUOUS_MA, description="Whole mA; device truncates any fraction."
    )
    pulse_ma: float | None = Field(
        None, ge=0, le=_MAX_HIGH_MA, description="Whole mA; device truncates any fraction."
    )
    voltage_mv: int | None = Field(None, ge=_MIN_VOLTAGE_MV, le=_MAX_VOLTAGE_MV)


class LimitsOut(BaseModel):
    continuous_ma: float
    pulse_ma: float
    voltage_mv: int


class TriggerInputIn(BaseModel):
    """Trigger/switch input config. Only the fields you send are written."""

    pulse_edge: EdgeName | None = None
    switch_active_high: bool | None = None
    switch_current_ma: float | None = Field(None, ge=0, le=_MAX_HIGH_MA)


class TriggerInputOut(BaseModel):
    pulse_edge: EdgeName
    switch_active_high: bool
    switch_current_ma: float


class TriggerOutputIn(BaseModel):
    """Trigger output config. Only the fields you send are written."""

    enabled: bool | None = None
    polarity: OutputPolarityName | None = None
    source: SourceName | None = None
    type: OutputTypeName | None = None
    delay_us: int | None = Field(None, ge=_MIN_OUTPUT_DELAY_US, le=_MAX_OUTPUT_DELAY_US)
    length_us: int | None = Field(None, ge=_MIN_OUTPUT_LENGTH_US, le=_MAX_OUTPUT_LENGTH_US)


class TriggerOutputOut(BaseModel):
    enabled: bool
    polarity: OutputPolarityName  # output fires on a single edge — never 'both'
    source: SourceName
    type: OutputTypeName
    delay_us: int
    length_us: int


# --- channel reads ------------------------------------------------------


class ChannelReadings(BaseModel):
    """All per-channel READ values; every field optional (curated vs full)."""

    temperature_c: float | None = None
    mode: ModeName | None = None
    pulse_current_ma: float | None = None
    switch_current_ma: float | None = None
    current_flow_ma: float | None = None
    continuous_limit_ma: float | None = None
    pulse_limit_ma: float | None = None
    voltage_limit_mv: int | None = None
    pulse_width_us: int | None = None
    pulse_delay_us: int | None = None
    cooling_time: int | None = None
    led_voltage_mv: int | None = None
    led_voltage_in_mv: int | None = None
    led_voltage_out_mv: int | None = None
    last_pulse_voltage_mv: int | None = None
    last_pulse_current_ma: float | None = None
    pulse_input_polarity: EdgeName | None = None
    switch_input_active_high: bool | None = None
    output_enabled: bool | None = None
    output_polarity: OutputPolarityName | None = None
    output_source: SourceName | None = None
    output_type: OutputTypeName | None = None
    output_delay_us: int | None = None
    output_length_us: int | None = None
    persisted: bool | None = None


class ChannelDetail(BaseModel):
    """Channel identity + a (curated or full) set of readings."""

    channel_num: int
    offset: int
    local_index: int
    readings: ChannelReadings
    unavailable: list[str] = Field(
        default_factory=list,
        description="Reads the device rejected for this mode/state (best-effort).",
    )


class ChannelSummary(BaseModel):
    """One row of ``GET /channels`` — identity plus a few live values."""

    channel_num: int
    offset: int
    local_index: int
    mode: ModeName | None = None
    temperature_c: float | None = None
    persisted: bool | None = None
    unavailable: list[str] = Field(default_factory=list)


class FieldRead(BaseModel):
    """A single granular read: ``GET /channels/{n}/{field}``."""

    field: str
    value: bool | int | float | str | None


# --- controllers / chain / status --------------------------------------


class ControllerOut(BaseModel):
    offset: int
    is_master: bool
    channels: list[int]


class EventOut(BaseModel):
    """An unsolicited device notice (``:E`` error / ``:S`` status)."""

    id: int
    ts: float
    kind: str
    message: str | None = None
    raw: str | None = None


class ChainIdentity(BaseModel):
    """Master identity + chain topology (curated or full)."""

    firmware: str | None = None
    serial: str | None = None
    mac: str | None = None
    ip: str | None = None
    subnet: str | None = None
    udp_port: int | None = None
    bootloader: str | None = None
    pcb_revision_control: str | None = None
    pcb_revision_power: str | None = None
    supply_voltage_mv: int | None = None
    controller_offset: int | None = None
    error_buffer: str | None = None
    persisted: bool | None = None
    offsets: list[int] = Field(default_factory=list)
    online_channels: list[int] = Field(default_factory=list)
    unavailable: list[str] = Field(default_factory=list)


class RootStatus(BaseModel):
    """``GET /`` — service + chain status (cached identity, no device I/O)."""

    service: str = "lucon-api"
    version: str
    ready: bool
    host: str
    port: int
    serial: str | None = None
    firmware: str | None = None
    controller_offset: int | None = None
    offsets: list[int] = Field(default_factory=list)
    online_channels: list[int] = Field(default_factory=list)


class ConfigIn(BaseModel):
    current_tenths: bool


class ConfigOut(BaseModel):
    current_tenths: bool


# --- chain actions ------------------------------------------------------


class NetworkIn(BaseModel):
    """Change the master's network identity. Destructive — may orphan the gateway.

    ``confirm`` must equal the device serial. When only ``ip`` is given it is
    applied via the serial-checked command (mirrors ``set_ip_checked``).
    """

    ip: str | None = None
    subnet: str | None = None
    controller_offset: int | None = Field(None, ge=0)
    confirm: str = Field(..., description="Must equal the device serial.")


class SaveIn(BaseModel):
    scope: int = Field(..., ge=0, le=2, description="0=general, 1=general+channel, 2=all channels.")


class FactoryResetIn(BaseModel):
    scope: int = Field(..., ge=0, le=2)
    confirm: str = Field(..., description="Must equal the device serial.")


class RestartIn(BaseModel):
    confirm: str = Field(..., description="Must equal the device serial.")


class SaveAndRestartIn(BaseModel):
    scope: int = Field(..., ge=0, le=2)
    confirm: str = Field(..., description="Must equal the device serial.")


# --- raw escape hatch ---------------------------------------------------


class RawIn(BaseModel):
    """Structured raw command (built through the codec; opt-in via LUCON_ENABLE_RAW)."""

    verb: Literal["S", "R"]
    channel: int = Field(..., ge=0, le=99)
    cmd: str = Field(..., min_length=1)
    values: list[str] = Field(default_factory=list, max_length=16)


class RawOut(BaseModel):
    kind: str
    echo: str | None = None
    values: list[str] = Field(default_factory=list)
    message: str | None = None
    raw: str


# --- generic acks -------------------------------------------------------


class WriteAck(BaseModel):
    ok: bool = True
    warning: str | None = None


class ReconnectAck(BaseModel):
    requested: bool = True
    ready: bool


def detail_value(value: Any) -> bool | int | float | str | None:
    """Coerce a reading to a JSON scalar for :class:`FieldRead` (enums -> str)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, str)):
        return value
    return str(value)
