"""Tests for :class:`lucon.testing.FakeLucon`.

FakeLucon is an in-process UDP simulator of a LUCON master Controller. These
tests drive it with a *plain* UDP client socket (no transport/domain layer,
which does not exist yet), exercising the observable wire behaviour: SET echo,
configured/default READ replies, command failure injection, and unsolicited
:E/:S notices reaching a listening client.

All sockets bind to 127.0.0.1:0 (OS-assigned ephemeral port) per the project
rules so parallel test processes never clash.
"""

import socket

import pytest

from lucon import codec
from lucon.testing import FakeLucon


def _client() -> socket.socket:
    """A plain UDP client socket bound to an ephemeral loopback port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(2.0)
    return sock


def test_start_returns_bound_loopback_address() -> None:
    fake = FakeLucon()
    host, port = fake.start()
    try:
        assert host == "127.0.0.1"
        assert port != 0
        assert fake.address == (host, port)
    finally:
        fake.stop()


def test_context_manager_starts_and_stops() -> None:
    with FakeLucon() as fake:
        host, port = fake.address
        assert host == "127.0.0.1"
        assert port != 0
    # After the block the address is no longer valid.
    with pytest.raises(RuntimeError):
        _ = fake.address


def test_start_is_idempotent_returning_same_address() -> None:
    fake = FakeLucon()
    try:
        first = fake.start()
        second = fake.start()
        assert first == second
    finally:
        fake.stop()


def test_stop_is_idempotent() -> None:
    fake = FakeLucon()
    fake.start()
    fake.stop()
    fake.stop()  # second stop must not raise


# --- SET handling ----------------------------------------------------------


def test_set_command_is_echoed_with_terminator() -> None:
    # A SET ack echoes the received command line verbatim, plus the '>' sentinel.
    with FakeLucon() as fake:
        client = _client()
        try:
            client.sendto(b"S01MC|100\r\n", fake.address)
            reply, _ = client.recvfrom(4096)
        finally:
            client.close()
    assert reply == b"S01MC|100\r\n>"
    # And it decodes as a clean SET ack through the proven codec.
    resp = codec.decode(reply)
    assert resp.kind is codec.ResponseKind.SET_ACK
    assert resp.echo == "S01MC|100"


def test_set_command_with_no_value_is_echoed() -> None:
    # e.g. S01MN (none/idle mode) carries no pipe value.
    with FakeLucon() as fake:
        client = _client()
        try:
            client.sendto(b"S01MN\r\n", fake.address)
            reply, _ = client.recvfrom(4096)
        finally:
            client.close()
    assert reply == b"S01MN\r\n>"


# --- READ handling ---------------------------------------------------------


def _read(fake: FakeLucon, line: bytes) -> codec.Response:
    """Send a READ line from a fresh client and decode the single reply."""
    client = _client()
    try:
        client.sendto(line, fake.address)
        reply, _ = client.recvfrom(4096)
    finally:
        client.close()
    return codec.decode(reply)


def test_configured_read_is_returned() -> None:
    # set_read scripts the value the device should report for a (channel, cmd).
    with FakeLucon() as fake:
        fake.set_read(1, "T", "37")
        resp = _read(fake, b"R01T\r\n")
    assert resp.kind is codec.ResponseKind.READ_REPLY
    assert resp.echo == "R01T"
    assert resp.values == ("37",)


def test_default_firmware_read_supports_handshake() -> None:
    # R00F is the mandatory connect-time handshake; it must answer by default.
    with FakeLucon() as fake:
        resp = _read(fake, b"R00F\r\n")
    assert resp.kind is codec.ResponseKind.READ_REPLY
    assert resp.echo == "R00F"
    assert resp.values and resp.values[0]  # a non-empty firmware string


def test_default_online_channels_read() -> None:
    # R00RT lists the online channels; default set is {1, 2, 3, 4}.
    with FakeLucon() as fake:
        resp = _read(fake, b"R00RT\r\n")
    assert resp.values == ("Online: 01, 02, 03, 04",)


def test_online_channels_are_configurable() -> None:
    with FakeLucon(online_channels={1, 2, 14}) as fake:
        resp = _read(fake, b"R00RT\r\n")
    assert resp.values == ("Online: 01, 02, 14",)


def test_set_then_read_reflects_last_written_value() -> None:
    # A SET lands in temporary memory; the natural READ counterpart reflects it.
    with FakeLucon() as fake:
        client = _client()
        try:
            client.sendto(b"S01MC|250\r\n", fake.address)
            client.recvfrom(4096)  # SET ack
        finally:
            client.close()
        resp = _read(fake, b"R01CA\r\n")
    assert resp.values == ("250",)


def test_configured_identity_reads() -> None:
    with FakeLucon(serial="SN-123", mac="AA:BB:CC:DD:EE:FF") as fake:
        sn = _read(fake, b"R00SN\r\n")
        mac = _read(fake, b"R00MAC\r\n")
    assert sn.values == ("SN-123",)
    assert mac.values == ("AA:BB:CC:DD:EE:FF",)


# --- fault injection -------------------------------------------------------


def test_fail_next_makes_next_command_error() -> None:
    with FakeLucon() as fake:
        fake.fail_next("Value out of range")
        client = _client()
        try:
            client.sendto(b"S01MC|9999\r\n", fake.address)
            reply, _ = client.recvfrom(4096)
        finally:
            client.close()
    resp = codec.decode(reply)
    assert resp.kind is codec.ResponseKind.ERROR
    assert resp.message == "Value out of range"


def test_fail_next_is_consumed_after_one_command() -> None:
    # Only the *next* matching command fails; the following one succeeds.
    with FakeLucon() as fake:
        fake.fail_next("boom")
        client = _client()
        try:
            client.sendto(b"S01MC|100\r\n", fake.address)
            first, _ = client.recvfrom(4096)
            client.sendto(b"S01MC|100\r\n", fake.address)
            second, _ = client.recvfrom(4096)
        finally:
            client.close()
    assert codec.decode(first).kind is codec.ResponseKind.ERROR
    assert codec.decode(second).kind is codec.ResponseKind.SET_ACK


def test_fail_next_with_non_ascii_message_does_not_kill_the_server() -> None:
    # CONTEXT.md flags :E text as firmware/locale-dependent (German manual,
    # umlauts plausible). A non-ASCII failure message must still produce a
    # reply and must not silently kill the RX thread for every later command.
    with FakeLucon() as fake:
        fake.fail_next("Wert ungültig: Überlauf")
        client = _client()
        try:
            client.sendto(b"S01MC|100\r\n", fake.address)
            reply, _ = client.recvfrom(4096)
            first = codec.decode(reply)
            # The server is still alive: a subsequent command still answers.
            client.sendto(b"R00F\r\n", fake.address)
            second_raw, _ = client.recvfrom(4096)
        finally:
            client.close()
    assert first.kind is codec.ResponseKind.ERROR
    assert codec.decode(second_raw).kind is codec.ResponseKind.READ_REPLY


def test_inject_with_non_ascii_message_does_not_raise() -> None:
    # Injected umlaut error text must be transmitted, not crash the injector.
    with FakeLucon() as fake:
        client = _client()
        try:
            client.sendto(b"R00F\r\n", fake.address)
            client.recvfrom(4096)
            fake.inject_error("Übertemperatur an Kanal 01")
            notice, _ = client.recvfrom(4096)
        finally:
            client.close()
    assert codec.decode(notice).kind is codec.ResponseKind.ERROR


def test_malformed_datagram_gets_an_error_reply() -> None:
    # The real device answers a bad command with ':E <msg>' rather than going
    # silent, so the fake must too (otherwise a client test hangs to timeout).
    with FakeLucon() as fake:
        client = _client()
        try:
            client.sendto(b"X01garbage\r\n", fake.address)
            reply, _ = client.recvfrom(4096)
        finally:
            client.close()
    resp = codec.decode(reply)
    assert resp.kind is codec.ResponseKind.ERROR
    assert resp.message  # a non-empty diagnostic


def test_inject_after_stop_raises_runtime_error_not_oserror() -> None:
    # A late inject (socket torn down) must surface a clean RuntimeError, never
    # an unguarded OSError from sending on a closed fd.
    fake = FakeLucon()
    fake.start()
    client = _client()
    try:
        client.sendto(b"R00F\r\n", fake.address)
        client.recvfrom(4096)
    finally:
        client.close()
    fake.stop()
    with pytest.raises(RuntimeError):
        fake.inject_error("too late")


def test_fail_next_can_target_a_specific_command() -> None:
    # A scoped failure only fires for the matching (channel, cmd); others pass.
    with FakeLucon() as fake:
        fake.fail_next("nope", channel=2, cmd="T")
        client = _client()
        try:
            client.sendto(b"R01T\r\n", fake.address)  # different channel: ok
            other, _ = client.recvfrom(4096)
            client.sendto(b"R02T\r\n", fake.address)  # matches: errors
            hit, _ = client.recvfrom(4096)
        finally:
            client.close()
    assert codec.decode(other).kind is codec.ResponseKind.READ_REPLY
    assert codec.decode(hit).kind is codec.ResponseKind.ERROR
    assert codec.decode(hit).message == "nope"


def test_inject_error_reaches_the_last_remote_station() -> None:
    with FakeLucon() as fake:
        client = _client()
        try:
            # The client must contact the fake first so it learns the remote.
            client.sendto(b"R00F\r\n", fake.address)
            client.recvfrom(4096)  # firmware reply
            fake.inject_error("Overtemperature on Channel 01")
            notice, _ = client.recvfrom(4096)
        finally:
            client.close()
    resp = codec.decode(notice)
    assert resp.kind is codec.ResponseKind.ERROR
    assert resp.message == "Overtemperature on Channel 01"


def test_inject_status_reaches_the_last_remote_station() -> None:
    with FakeLucon() as fake:
        client = _client()
        try:
            client.sendto(b"R00F\r\n", fake.address)
            client.recvfrom(4096)
            fake.inject_status("RUNNING...")
            notice, _ = client.recvfrom(4096)
        finally:
            client.close()
    resp = codec.decode(notice)
    assert resp.kind is codec.ResponseKind.STATUS
    assert resp.message == "RUNNING..."


def test_inject_targets_the_most_recent_sender() -> None:
    # The device only replies to the last station that contacted it.
    with FakeLucon() as fake:
        first = _client()
        second = _client()
        try:
            first.sendto(b"R00F\r\n", fake.address)
            first.recvfrom(4096)
            second.sendto(b"R00F\r\n", fake.address)
            second.recvfrom(4096)
            fake.inject_error("late")
            notice, _ = second.recvfrom(4096)
            first.settimeout(0.3)
            with pytest.raises(TimeoutError):
                first.recvfrom(4096)
        finally:
            first.close()
            second.close()
    assert codec.decode(notice).message == "late"


def test_inject_before_any_contact_raises() -> None:
    with FakeLucon() as fake:
        with pytest.raises(RuntimeError):
            fake.inject_error("no one is listening yet")
        with pytest.raises(RuntimeError):
            fake.inject_status("nobody home")


# --- clean shutdown --------------------------------------------------------


def test_stop_joins_the_server_thread() -> None:
    import threading

    before = threading.active_count()
    fake = FakeLucon()
    fake.start()
    # Exercise the thread so it is genuinely running.
    client = _client()
    try:
        client.sendto(b"R00F\r\n", fake.address)
        client.recvfrom(4096)
    finally:
        client.close()
    fake.stop()
    # The daemon server thread is gone after stop().
    assert threading.active_count() == before


def test_stopped_fake_no_longer_replies() -> None:
    fake = FakeLucon()
    addr = fake.start()
    fake.stop()
    client = _client()
    client.settimeout(0.3)
    try:
        client.sendto(b"R00F\r\n", addr)
        with pytest.raises((TimeoutError, ConnectionError, OSError)):
            client.recvfrom(4096)
    finally:
        client.close()


def test_restart_after_stop_binds_a_fresh_socket() -> None:
    # start() after stop() must work again and serve requests.
    fake = FakeLucon()
    fake.start()
    fake.stop()
    fake.start()
    try:
        resp = _read(fake, b"R00F\r\n")
        assert resp.kind is codec.ResponseKind.READ_REPLY
    finally:
        fake.stop()
