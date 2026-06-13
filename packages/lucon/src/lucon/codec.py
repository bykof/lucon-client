"""Pure wire-protocol codec for the LUCON 4C-20A-V.

Encodes command lines to the bytes sent on the wire and decodes the datagrams
received back. This layer is deliberately thin: it knows the protocol grammar
and wire formats, but not the meaning of any particular command. Domain meaning
lives in the ``Lucon``/``Controller``/``Channel`` objects above it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from lucon.exceptions import LuconProtocolError

# The device accepts \r\n, \r, or \n as a line delimiter. We always *send* the
# canonical CRLF; the decoder tolerates all three on the way back.
DELIMITER = "\r\n"

# Every device response is terminated by this sentinel character.
TERMINATOR = ">"


class ResponseKind(enum.Enum):
    """How the device datagram is classified by the codec."""

    SET_ACK = "set_ack"  # echoed SET command, e.g. ``S01MC|100\r\n>``
    READ_REPLY = "read_reply"  # echoed READ command + value(s)
    ERROR = "error"  # ``:E <message>`` (solicited command error or unsolicited)
    STATUS = "status"  # ``:S <message>`` async status notice


@dataclass(frozen=True)
class Response:
    """A decoded device datagram.

    ``echo`` carries the echoed command for SET/READ replies; ``values`` the
    value token(s) of a READ reply; ``message`` the human-readable text of a
    ``:E``/``:S`` notice. ``raw`` is always the original bytes, preserved so the
    transport/exception layer can surface the device's exact wording.
    """

    kind: ResponseKind
    raw: bytes
    echo: str | None = None
    values: tuple[str, ...] = ()
    message: str | None = None


@dataclass(frozen=True)
class Command:
    """A parsed request line — the inverse of :func:`encode_set`/:func:`encode_read`."""

    verb: str  # "S" (SET) or "R" (READ)
    channel: int
    cmd: str
    values: tuple[str, ...] = ()


# The address field is exactly two digits, and the device accepts at most 16
# pipe-separated values per command line.
MAX_VALUES = 16


def _encode(verb: str, channel: int, cmd: str, values: tuple[str, ...]) -> bytes:
    if not 0 <= channel <= 99:
        raise ValueError(f"channel must be 0-99, got {channel}")
    if len(values) > MAX_VALUES:
        raise ValueError(f"at most {MAX_VALUES} values allowed, got {len(values)}")
    line = f"{verb}{channel:02d}{cmd}" + "".join(f"|{v}" for v in values)
    return (line + DELIMITER).encode("ascii")


def encode_set(channel: int, cmd: str, *values: str) -> bytes:
    """Build a SET command line: ``S<cc><cmd>(|<value>)*<delimiter>``."""
    return _encode("S", channel, cmd, values)


def encode_read(channel: int, cmd: str, *values: str) -> bytes:
    """Build a READ command line: ``R<cc><cmd>(|<value>)*<delimiter>``."""
    return _encode("R", channel, cmd, values)


def parse_command(data: bytes) -> Command:
    """Parse a request line into a :class:`Command` (the inverse of encoding).

    Used by the protocol simulator to interpret incoming datagrams. Raises
    :class:`~lucon.exceptions.LuconProtocolError` on a malformed line.
    """
    text = data.decode("ascii", errors="replace").rstrip("\r\n")
    if len(text) < 3 or text[0] not in ("S", "R"):
        raise LuconProtocolError(
            "command must start with S/R + 2-digit channel", raw=data
        )
    channel_field = text[1:3]
    if not channel_field.isdigit():
        raise LuconProtocolError("command channel must be two digits", raw=data)
    parts = text[3:].split("|")
    if not parts[0]:
        raise LuconProtocolError("command mnemonic missing", raw=data)
    return Command(
        verb=text[0], channel=int(channel_field), cmd=parts[0], values=tuple(parts[1:])
    )


# At or below this current, the device works in 0.1 mA steps; above it, whole mA.
FINE_CURRENT_MAX_MA = 45.0


def format_current(ma: float) -> str:
    """Format a current in mA for the wire.

    Snaps to the device's resolution: 0.1 mA at or below 45 mA, whole mA above.
    """
    if ma < 0:
        raise ValueError(f"current must be non-negative, got {ma}")
    if ma <= FINE_CURRENT_MAX_MA:
        return f"{round(ma, 1):.1f}"
    return str(round(ma))


def parse_current(text: str, *, tenths: bool = False) -> float:
    """Parse a current reading (in mA) from a READ value token.

    ``tenths`` selects the integer-tenths interpretation (``"354"`` -> 35.4 mA).
    A literal decimal point is unambiguous and always wins, regardless of
    ``tenths``. **Confirmed on hardware (fw 0.5.0):** driven currents report
    sub-45 mA values as *decimal* mA (e.g. ``"10.9"``, ``"1.0"``), so the default
    ``tenths=False`` is correct; the flag is retained as a hedge for firmware
    that may report integer tenths. (CONTEXT.md resolved open item #1.)
    """
    text = text.strip()
    value = float(text)  # raises ValueError on non-numeric input
    if tenths and "." not in text:
        return value / 10.0
    return value


def decode(data: bytes) -> Response:
    """Classify a single device datagram into a :class:`Response`.

    Raises :class:`~lucon.exceptions.LuconProtocolError` if ``data`` is not a
    valid, terminated, non-empty response.
    """
    text = data.decode("ascii", errors="replace")
    if not text.endswith(TERMINATOR):
        raise LuconProtocolError("response not terminated by '>'", raw=data)
    body = text[: -len(TERMINATOR)]  # drop the trailing '>'
    # Normalize any delimiter to '\n', then split into stripped, non-empty lines.
    normalized = body.replace("\r\n", "\n").replace("\r", "\n")
    lines = [stripped for ln in normalized.split("\n") if (stripped := ln.strip())]
    if not lines:
        raise LuconProtocolError("empty response", raw=data)
    echo = lines[0]
    if echo.startswith(":E"):
        return Response(
            kind=ResponseKind.ERROR, raw=data, message=echo.removeprefix(":E").strip()
        )
    if echo.startswith(":S"):
        return Response(
            kind=ResponseKind.STATUS, raw=data, message=echo.removeprefix(":S").strip()
        )
    if echo.startswith("R"):
        return Response(
            kind=ResponseKind.READ_REPLY,
            raw=data,
            echo=echo,
            values=tuple(lines[1:]),
        )
    if echo.startswith("S"):
        return Response(kind=ResponseKind.SET_ACK, raw=data, echo=echo)
    raise LuconProtocolError("unclassifiable response", raw=data)
