"""API-facing string enums and their mapping to/from the core ``lucon`` enums.

The wire tokens (``"2"``, ``"R"``, …) never leak into JSON: HTTP clients see the
glossary's canonical names (CONTEXT.md — continuous/switch/pulse/none, rising/
falling/both, input/lighting, …) and this module converts them to the
:mod:`lucon.enums` members the client library expects.
"""

from __future__ import annotations

from enum import StrEnum

from lucon import Mode, OutputTriggerSource, OutputTriggerType, TriggerEdge


class ModeName(StrEnum):
    """Operating mode names (CONTEXT.md). ``ERROR`` is read-only."""

    NONE = "none"
    CONTINUOUS = "continuous"
    SWITCH = "switch"
    PULSE = "pulse"
    ERROR = "error"


class EdgeName(StrEnum):
    """Trigger edge / polarity for the pulse input (``rising``/``falling``/``both``)."""

    RISING = "rising"
    FALLING = "falling"
    BOTH = "both"


class OutputPolarityName(StrEnum):
    """Trigger *output* polarity — single edge only; ``both`` is invalid for an output."""

    RISING = "rising"
    FALLING = "falling"


class SourceName(StrEnum):
    """Trigger output source: driven by the input, or by the lighting state."""

    INPUT = "input"
    LIGHTING = "lighting"


class OutputTypeName(StrEnum):
    """Trigger output type: fixed length, or held while the channel is lit."""

    TIME_LIMITED = "time_limited"
    WHILE_LIT = "while_lit"


_MODE_TO_NAME: dict[Mode, ModeName] = {
    Mode.NONE: ModeName.NONE,
    Mode.CONTINUOUS: ModeName.CONTINUOUS,
    Mode.SWITCH: ModeName.SWITCH,
    Mode.PULSE: ModeName.PULSE,
    Mode.ERROR: ModeName.ERROR,
}

_EDGE_TO_NAME: dict[TriggerEdge, EdgeName] = {
    TriggerEdge.RISING: EdgeName.RISING,
    TriggerEdge.FALLING: EdgeName.FALLING,
    TriggerEdge.BOTH: EdgeName.BOTH,
}
_NAME_TO_EDGE: dict[str, TriggerEdge] = {v: k for k, v in _EDGE_TO_NAME.items()}

_SOURCE_TO_NAME: dict[OutputTriggerSource, SourceName] = {
    OutputTriggerSource.INPUT: SourceName.INPUT,
    OutputTriggerSource.LIGHTING: SourceName.LIGHTING,
}
_NAME_TO_SOURCE: dict[str, OutputTriggerSource] = {v: k for k, v in _SOURCE_TO_NAME.items()}

_TYPE_TO_NAME: dict[OutputTriggerType, OutputTypeName] = {
    OutputTriggerType.TIME_LIMITED: OutputTypeName.TIME_LIMITED,
    OutputTriggerType.WHILE_LIT: OutputTypeName.WHILE_LIT,
}
_NAME_TO_TYPE: dict[str, OutputTriggerType] = {v: k for k, v in _TYPE_TO_NAME.items()}


def mode_name(mode: Mode) -> ModeName:
    """Map a :class:`lucon.Mode` to its API name."""
    return _MODE_TO_NAME[mode]


def edge_name(edge: TriggerEdge) -> EdgeName:
    """Map a :class:`lucon.TriggerEdge` to its API name."""
    return _EDGE_TO_NAME[edge]


def edge_from_name(name: str) -> TriggerEdge:
    """Map an API edge name to a :class:`lucon.TriggerEdge`."""
    return _NAME_TO_EDGE[name]


def output_polarity_from_name(name: str) -> TriggerEdge:
    """Map an API output-polarity name to a :class:`lucon.TriggerEdge` (rising/falling)."""
    return _NAME_TO_EDGE[name]


def output_polarity_name(edge: TriggerEdge) -> OutputPolarityName:
    """Map a trigger-output edge read-back to its API name (rising/falling only).

    An output fires on a single edge, so ``BOTH`` is invalid; a device reporting
    it is a protocol error (raised here, surfaced as 502), not a value the read
    schema should advertise as round-trippable.
    """
    if edge is TriggerEdge.RISING:
        return OutputPolarityName.RISING
    if edge is TriggerEdge.FALLING:
        return OutputPolarityName.FALLING
    raise ValueError(f"output polarity must be rising or falling, got {edge.name.lower()}")


def source_name(source: OutputTriggerSource) -> SourceName:
    """Map a :class:`lucon.OutputTriggerSource` to its API name."""
    return _SOURCE_TO_NAME[source]


def source_from_name(name: str) -> OutputTriggerSource:
    """Map an API source name to a :class:`lucon.OutputTriggerSource`."""
    return _NAME_TO_SOURCE[name]


def output_type_name(type_: OutputTriggerType) -> OutputTypeName:
    """Map a :class:`lucon.OutputTriggerType` to its API name."""
    return _TYPE_TO_NAME[type_]


def output_type_from_name(name: str) -> OutputTriggerType:
    """Map an API type name to a :class:`lucon.OutputTriggerType`."""
    return _NAME_TO_TYPE[name]
