"""Tests for the LUCON wire-protocol codec.

The codec is a pure, thin layer: it assembles command lines into the bytes that
go on the wire, and classifies the datagrams that come back. Domain meaning
(which command carries a current, what a mode number means) lives above it.

Grammar (from CONTEXT.md / the manual):
    ('S'|'R') + <2-digit channel> + <cmd> + ('|'<value>)* + <delimiter>
"""

import pytest

from lucon import codec
from lucon.exceptions import LuconProtocolError


def test_encode_set_builds_command_line() -> None:
    # S = SET, channel 01, command MC (continuous current), value 100.
    assert codec.encode_set(1, "MC", "100") == b"S01MC|100\r\n"


def test_encode_read_builds_query_line() -> None:
    # R = READ, channel 01, command T (temperature), no values.
    assert codec.encode_read(1, "T") == b"R01T\r\n"


def test_encode_addresses_general_channel_as_00() -> None:
    # Channel 00 is the device-wide / master "general" address.
    assert codec.encode_read(0, "F") == b"R00F\r\n"
    assert codec.encode_set(0, "R", "1") == b"S00R|1\r\n"


def test_encode_joins_multiple_pipe_values() -> None:
    # MDU pulse takes |current|delay_us|duration_us.
    assert codec.encode_set(1, "MDU", "1000", "30", "500") == b"S01MDU|1000|30|500\r\n"


@pytest.mark.parametrize("channel", [-1, 100, 250])
def test_encode_rejects_out_of_range_channel(channel: int) -> None:
    # Channel must fit the 2-digit address field (00-99).
    with pytest.raises(ValueError):
        codec.encode_set(channel, "MC", "100")


def test_encode_rejects_too_many_values() -> None:
    # The device accepts at most 16 pipe-separated values.
    codec.encode_set(1, "X", *(["1"] * 16))  # 16 is fine
    with pytest.raises(ValueError):
        codec.encode_set(1, "X", *(["1"] * 17))


# --- decoding --------------------------------------------------------------
#
# Every device response ends in '>'. A SET ack echoes the command; a READ reply
# echoes the command then carries the value(s); ':E' is an error notice and
# ':S' an async status notice.


def test_decode_set_ack_echoes_command() -> None:
    resp = codec.decode(b"S01MC|100\r\n>")
    assert resp.kind is codec.ResponseKind.SET_ACK
    assert resp.echo == "S01MC|100"
    assert resp.values == ()
    assert resp.raw == b"S01MC|100\r\n>"


def test_decode_read_reply_carries_value() -> None:
    # R01T\r\n31\r\n> : echo "R01T", then the value "31".
    resp = codec.decode(b"R01T\r\n31\r\n>")
    assert resp.kind is codec.ResponseKind.READ_REPLY
    assert resp.echo == "R01T"
    assert resp.values == ("31",)


def test_decode_set_ack_with_no_value() -> None:
    # e.g. S01MN (none/idle mode) or S00R (restart) carry no pipe value.
    resp = codec.decode(b"S01MN\r\n>")
    assert resp.kind is codec.ResponseKind.SET_ACK
    assert resp.echo == "S01MN"
    assert resp.values == ()


def test_decode_read_reply_keeps_a_list_value_as_one_token() -> None:
    # R00RT returns a single line listing online channels; the codec hands the
    # whole line back as one value token for the domain layer to split.
    resp = codec.decode(b"R00RT\r\nOnline: 01, 02, 14\r\n>")
    assert resp.kind is codec.ResponseKind.READ_REPLY
    assert resp.values == ("Online: 01, 02, 14",)


def test_decode_read_reply_with_multiple_value_lines() -> None:
    # A multi-line read (e.g. the error buffer) yields one token per line.
    resp = codec.decode(b"R00M\r\nfault A\r\nfault B\r\n>")
    assert resp.kind is codec.ResponseKind.READ_REPLY
    assert resp.echo == "R00M"
    assert resp.values == ("fault A", "fault B")


def test_decode_error_notice_preserves_message() -> None:
    # ':E <msg>' — we never parse the text (firmware/locale may vary); we keep
    # the message and the raw bytes verbatim.
    resp = codec.decode(b":E Overtemperature on Channel 01\r\n>")
    assert resp.kind is codec.ResponseKind.ERROR
    assert resp.message == "Overtemperature on Channel 01"
    assert resp.echo is None
    assert resp.raw == b":E Overtemperature on Channel 01\r\n>"


def test_decode_status_notice() -> None:
    # ':S <msg>' — async status (e.g. emitted after boot).
    resp = codec.decode(b":S RUNNING...\r\n>")
    assert resp.kind is codec.ResponseKind.STATUS
    assert resp.message == "RUNNING..."


@pytest.mark.parametrize("delim", [b"\r\n", b"\r", b"\n"])
def test_decode_normalizes_any_delimiter(delim: bytes) -> None:
    # The device may frame with CRLF, CR, or LF; all decode identically.
    resp = codec.decode(b"R01T" + delim + b"31" + delim + b">")
    assert resp.kind is codec.ResponseKind.READ_REPLY
    assert resp.echo == "R01T"
    assert resp.values == ("31",)


@pytest.mark.parametrize(
    "data",
    [
        b"S01MC|100\r\n",  # missing the trailing '>'
        b"",  # empty datagram
        b">",  # terminator only, no content
        b"   \r\n>",  # only whitespace before terminator
    ],
)
def test_decode_rejects_malformed_frame(data: bytes) -> None:
    with pytest.raises(LuconProtocolError) as info:
        codec.decode(data)
    # The undecodable bytes are preserved for diagnostics.
    assert info.value.raw == data


def test_decode_rejects_unclassifiable_frame() -> None:
    # The device only ever begins a response with S, R, ':E', or ':S'. Anything
    # else is a protocol violation, not a silently-accepted SET ack.
    with pytest.raises(LuconProtocolError):
        codec.decode(b"GARBAGE\r\n>")


# --- current formatting ----------------------------------------------------
#
# The device accepts/reports 0.1 mA resolution at or below 45 mA, and whole-mA
# above. SET clearly uses decimal mA (e.g. S01MC|10.9).


@pytest.mark.parametrize(
    "ma, expected",
    [
        (0.0, "0.0"),
        (10.9, "10.9"),
        (10.94, "10.9"),  # snapped down to 0.1 mA resolution
        (10.96, "11.0"),  # snapped up, still <=45 -> one decimal
        (45.0, "45.0"),  # boundary: still 0.1 mA resolution
        (46.0, "46"),  # above 45 -> whole mA
        (100.0, "100"),
        (3000.0, "3000"),
    ],
)
def test_format_current(ma: float, expected: str) -> None:
    assert codec.format_current(ma) == expected


def test_format_current_rejects_negative() -> None:
    with pytest.raises(ValueError):
        codec.format_current(-1.0)


# --- current parsing (UNRESOLVED hardware ambiguity) -----------------------
#
# Open item #1 (CONTEXT.md): a sub-45 mA READ may return "35.4" (decimal mA) or
# "354" (integer tenths). We parse tolerantly and let a connect-time probe pick
# the mode. A decimal point is unambiguous (only decimal mA has one), so it wins
# regardless of mode. TODO: confirm the device's convention on real hardware.


@pytest.mark.parametrize(
    "text, expected", [("31", 31.0), ("35.4", 35.4), ("100", 100.0), ("0", 0.0)]
)
def test_parse_current_decimal_mode(text: str, expected: float) -> None:
    assert codec.parse_current(text) == pytest.approx(expected)


@pytest.mark.parametrize(
    "text, expected", [("354", 35.4), ("109", 10.9), ("300", 30.0)]
)
def test_parse_current_tenths_mode(text: str, expected: float) -> None:
    assert codec.parse_current(text, tenths=True) == pytest.approx(expected)


def test_parse_current_decimal_point_wins_over_tenths_mode() -> None:
    # A '.' can only mean decimal mA, so it overrides the tenths interpretation.
    assert codec.parse_current("35.4", tenths=True) == pytest.approx(35.4)


def test_parse_current_tolerates_whitespace() -> None:
    assert codec.parse_current("  31  ") == pytest.approx(31.0)


def test_parse_current_rejects_nonnumeric() -> None:
    with pytest.raises(ValueError):
        codec.parse_current("Online: 01")


# --- request parsing (inverse of encode_*, used by FakeLucon) --------------


def test_parse_command_set_with_value() -> None:
    cmd = codec.parse_command(b"S01MC|100\r\n")
    assert cmd.verb == "S"
    assert cmd.channel == 1
    assert cmd.cmd == "MC"
    assert cmd.values == ("100",)


def test_parse_command_read_general_no_value() -> None:
    cmd = codec.parse_command(b"R00F\r\n")
    assert cmd.verb == "R"
    assert cmd.channel == 0
    assert cmd.cmd == "F"
    assert cmd.values == ()


def test_parse_command_multiple_values() -> None:
    cmd = codec.parse_command(b"S01MDU|1000|30|500\r\n")
    assert cmd.cmd == "MDU"
    assert cmd.values == ("1000", "30", "500")


def test_parse_command_tolerates_any_or_missing_delimiter() -> None:
    assert codec.parse_command(b"R01T\r").cmd == "T"
    assert codec.parse_command(b"R01T\n").cmd == "T"
    assert codec.parse_command(b"R01T").cmd == "T"


@pytest.mark.parametrize("verb", ["S", "R"])
def test_encode_then_parse_command_round_trips(verb: str) -> None:
    encode = codec.encode_set if verb == "S" else codec.encode_read
    cmd = codec.parse_command(encode(7, "MC", "100", "200"))
    assert (cmd.verb, cmd.channel, cmd.cmd, cmd.values) == (
        verb,
        7,
        "MC",
        ("100", "200"),
    )


@pytest.mark.parametrize("data", [b"X01MC\r\n", b"S1\r\n", b"\r\n", b"SXYMC\r\n"])
def test_parse_command_rejects_malformed(data: bytes) -> None:
    with pytest.raises(LuconProtocolError):
        codec.parse_command(data)
