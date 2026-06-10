"""Python client for the GEFASOFT LUCON 4C-20A-V LED light controller.

Communicates over the device's UDP/Ethernet ASCII command protocol. See
``CONTEXT.md`` for the domain glossary and ``docs/adr/`` for the architectural
decisions that shape this package.

The public surface is the nested **Chain -> Controller -> Channel** model:
connect with :class:`Lucon`, navigate to a :class:`~lucon.controller.Controller`
by offset (or jump straight to a :class:`~lucon.channel.Channel` via the global
``channel(1..96)`` shortcut), and drive per-channel commands from there.
"""

from __future__ import annotations

from lucon.channel import Channel
from lucon.controller import Controller
from lucon.enums import Mode, OutputTriggerSource, OutputTriggerType, TriggerEdge
from lucon.exceptions import (
    LuconCommandError,
    LuconConnectionError,
    LuconError,
    LuconProtocolError,
    LuconTimeoutError,
)
from lucon.lucon import Lucon
from lucon.transport import probe

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # Domain tree
    "Lucon",
    "Controller",
    "Channel",
    # Enums
    "Mode",
    "TriggerEdge",
    "OutputTriggerSource",
    "OutputTriggerType",
    # Exceptions
    "LuconError",
    "LuconCommandError",
    "LuconTimeoutError",
    "LuconConnectionError",
    "LuconProtocolError",
    # Transport helper
    "probe",
]
