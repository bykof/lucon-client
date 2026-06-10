"""Best-effort composite reads shared by the channel and chain routes.

A composite snapshot (``?detail=full``, summary rows) must not 500 because one
field is unreadable in the current mode/state. So a per-field failure is recorded
under ``unavailable`` — but a whole-device transport failure (connection/timeout/
protocol) is re-raised so it maps to the right 503/504/502, never silently hidden.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, TypeVar

from lucon import (
    LuconCommandError,
    LuconConnectionError,
    LuconError,
    LuconProtocolError,
    LuconTimeoutError,
)

_LOG = logging.getLogger("lucon_api")

T = TypeVar("T")

# Failures that mean the whole device is unreachable — always propagate.
_PROPAGATE = (LuconConnectionError, LuconTimeoutError, LuconProtocolError)


def collect(
    target: T,
    readers: dict[str, Callable[[T], Any]],
    fields: list[str],
) -> tuple[dict[str, Any], list[str]]:
    """Read ``fields`` from ``target``; per-field errors -> ``unavailable``.

    Catches a per-field ``:E`` rejection (``LuconCommandError``), an empty/odd
    reply (bare ``LuconError``), or a value-parse failure (``ValueError``).
    Re-raises connection/timeout/protocol errors untouched.
    """
    readings: dict[str, Any] = {}
    unavailable: list[str] = []
    for name in fields:
        try:
            readings[name] = readers[name](target)
        except _PROPAGATE:
            raise
        except LuconCommandError as exc:
            # The device rejected this read with a :E. That can be a benign
            # mode-incompatible field OR a real fault (overtemp) — the :E text is
            # locale/firmware-dependent so we can't tell here. Log it so a fault
            # is traceable (it also reaches the SSE stream via on_error), then
            # mark the field unavailable rather than failing the whole snapshot.
            _LOG.warning("device rejected read %r: %s", name, exc)
            unavailable.append(name)
        except (LuconError, ValueError):
            unavailable.append(name)
    return readings, unavailable
