"""Tests for the exception hierarchy.

Lean hierarchy, raw message preserved. We never parse ':E' text into structured
fields (firmware/locale may vary) — we keep the device's wording verbatim.
"""

import pytest

from lucon import exceptions as exc


@pytest.mark.parametrize(
    "subclass",
    [
        exc.LuconCommandError,
        exc.LuconTimeoutError,
        exc.LuconConnectionError,
        exc.LuconProtocolError,
    ],
)
def test_all_errors_share_a_common_base(subclass: type[exc.LuconError]) -> None:
    # Callers can catch everything with `except LuconError`.
    assert issubclass(subclass, exc.LuconError)


def test_command_error_preserves_message_and_offending_command() -> None:
    err = exc.LuconCommandError(
        "Overtemperature on Channel 01",
        command=b"S01MC|100\r\n",
        raw=b":E Overtemperature on Channel 01\r\n>",
    )
    assert err.message == "Overtemperature on Channel 01"
    assert err.command == b"S01MC|100\r\n"
    assert err.raw == b":E Overtemperature on Channel 01\r\n>"
    # The message is also the str() of the exception.
    assert "Overtemperature on Channel 01" in str(err)


def test_protocol_error_preserves_raw_bytes() -> None:
    err = exc.LuconProtocolError("unterminated datagram", raw=b"S01MC|100\r\n")
    assert err.raw == b"S01MC|100\r\n"
