"""Tests for :class:`lucon.transport.Transport`.

These exercise the synchronous transport (ADR-0001) against a live
:class:`lucon.testing.FakeLucon` over genuine UDP loopback, so the
background RX thread, the request/reply demux, retries, and unsolicited
notice routing are all driven through the real socket path.

Per the project rules every socket binds to 127.0.0.1:0 (an OS-assigned
ephemeral port) so parallel test processes never clash, and we use stdlib
``logging`` rather than prints.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from lucon import codec
from lucon.codec import Response
from lucon.exceptions import (
    LuconCommandError,
    LuconConnectionError,
    LuconTimeoutError,
)
from lucon.testing import FakeLucon
from lucon.transport import Transport, probe


def _dead_port() -> int:
    """Return a loopback UDP port with nothing bound to it.

    We bind an ephemeral port, read it back, then close the socket — the
    port is free again and (almost certainly) still unused, so sending to
    it produces no reply.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port: int = sock.getsockname()[1]
    sock.close()
    return port


class _SilentAfterHandshake:
    """A UDP server that answers only the ``R00F`` handshake, then goes silent.

    It counts every datagram received after the handshake so a test can assert
    the transport retransmitted before giving up. Sockets bind to 127.0.0.1:0.
    """

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.settimeout(0.1)
        self.address: tuple[str, int] = self._sock.getsockname()
        self._stop = threading.Event()
        self.post_handshake_datagrams = 0
        # The last remote that contacted us, mirroring the real device.
        self.remote: tuple[str, int] | None = None
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def inject(self, datagram: bytes) -> None:
        """Send an unsolicited datagram to the last learned remote station."""
        if self.remote is None:
            raise RuntimeError("no remote learned yet")
        self._sock.sendto(datagram, self.remote)

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                data, remote = self._sock.recvfrom(4096)
            except (TimeoutError, OSError):
                continue
            self.remote = remote
            if data == codec.encode_read(0, "F"):
                reply = (
                    "R00F" + codec.DELIMITER + "fw" + codec.DELIMITER + codec.TERMINATOR
                ).encode("ascii")
                self._sock.sendto(reply, remote)
            else:
                # Silently drop every non-handshake command (count it first).
                self.post_handshake_datagrams += 1

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)
        self._sock.close()

    def __enter__(self) -> _SilentAfterHandshake:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class _DelayedReply:
    """A UDP server that answers the handshake instantly and delays *only the
    first* post-handshake reply by ``delay`` seconds (sent from a background
    timer so the RX loop keeps running); every later datagram is answered
    immediately.

    This models a slow-but-alive device: a retransmit gets a prompt reply while
    the original (delayed) reply arrives afterwards as a duplicate. Counts
    post-handshake datagrams. Sockets bind to 127.0.0.1:0.
    """

    def __init__(self, *, delay: float, reply: bytes) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.settimeout(0.1)
        self.address: tuple[str, int] = self._sock.getsockname()
        self._stop = threading.Event()
        self._delay = delay
        self._reply = reply
        self.post_handshake_datagrams = 0
        self._first = True
        self._timers: list[threading.Timer] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                data, remote = self._sock.recvfrom(4096)
            except (TimeoutError, OSError):
                continue
            if data == codec.encode_read(0, "F"):
                hs = (
                    "R00F" + codec.DELIMITER + "fw" + codec.DELIMITER + codec.TERMINATOR
                ).encode("ascii")
                self._sock.sendto(hs, remote)
                continue
            self.post_handshake_datagrams += 1
            if self._first:
                self._first = False
                timer = threading.Timer(
                    self._delay, self._sock.sendto, args=(self._reply, remote)
                )
                timer.daemon = True
                self._timers.append(timer)
                timer.start()
            else:
                self._sock.sendto(self._reply, remote)

    def close(self) -> None:
        self._stop.set()
        for timer in self._timers:
            timer.cancel()
        self._thread.join(timeout=2.0)
        self._sock.close()

    def __enter__(self) -> _DelayedReply:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# --- open()/close() handshake ---------------------------------------------


def test_open_performs_handshake_and_marks_open() -> None:
    with FakeLucon() as fake:
        host, port = fake.address
        transport = Transport(host, port, timeout=1.0)
        assert transport.is_open is False
        transport.open()
        try:
            assert transport.is_open is True
        finally:
            transport.close()
        assert transport.is_open is False


def test_open_against_dead_port_raises_connection_error() -> None:
    port = _dead_port()
    transport = Transport("127.0.0.1", port, timeout=0.2, retries=1)
    with pytest.raises(LuconConnectionError):
        transport.open()
    # A failed open leaves the transport closed (socket torn down).
    assert transport.is_open is False


# --- send()/query() round-trips -------------------------------------------


def test_send_returns_the_set_ack() -> None:
    with FakeLucon() as fake:
        host, port = fake.address
        with Transport(host, port) as transport:
            resp = transport.send(codec.encode_set(1, "MC", "100"))
    assert resp.kind is codec.ResponseKind.SET_ACK
    assert resp.echo == "S01MC|100"


def test_query_returns_the_read_reply_value() -> None:
    with FakeLucon() as fake:
        fake.set_read(1, "T", "31")
        host, port = fake.address
        with Transport(host, port) as transport:
            resp = transport.query(codec.encode_read(1, "T"))
    assert resp.kind is codec.ResponseKind.READ_REPLY
    assert resp.echo == "R01T"
    assert resp.values == ("31",)


# --- solicited device error -----------------------------------------------


def test_device_error_raises_command_error_with_context() -> None:
    command = codec.encode_set(1, "MC", "9999")
    with FakeLucon() as fake:
        host, port = fake.address
        with Transport(host, port) as transport:
            # Scope the failure to the SET so the R00F handshake still succeeds.
            fake.fail_next("Value out of range", channel=1, cmd="MC")
            with pytest.raises(LuconCommandError) as excinfo:
                transport.send(command)
    err = excinfo.value
    assert err.message == "Value out of range"
    assert err.command == command
    assert err.raw is not None
    # The raw device bytes are preserved verbatim and re-decodable.
    assert codec.decode(err.raw).message == "Value out of range"


# --- timeout + retry ------------------------------------------------------


def test_query_retries_then_raises_timeout() -> None:
    # Handshake succeeds, but the subsequent READ is dropped; the transport
    # must retransmit (retries=2 -> 3 attempts total) then time out.
    with _SilentAfterHandshake() as server:
        host, port = server.address
        with Transport(host, port, timeout=0.15, retries=2) as transport:
            with pytest.raises(LuconTimeoutError):
                transport.query(codec.encode_read(1, "T"))
    assert server.post_handshake_datagrams == 3


# --- unsolicited notices --------------------------------------------------


def test_inject_error_fires_on_error_and_is_pollable() -> None:
    received: list[Response] = []
    fired = threading.Event()

    def on_error(resp: Response) -> None:
        received.append(resp)
        fired.set()

    with FakeLucon() as fake:
        host, port = fake.address
        with Transport(host, port, on_error=on_error) as transport:
            # The handshake during open() registered us as the remote station.
            fake.inject_error("Overtemperature on Channel 01")
            assert fired.wait(2.0), "on_error callback did not fire"
            polled = transport.poll_events(timeout=2.0)

    assert len(received) == 1
    assert received[0].kind is codec.ResponseKind.ERROR
    assert received[0].message == "Overtemperature on Channel 01"
    assert polled is not None
    assert polled.kind is codec.ResponseKind.ERROR
    assert polled.message == "Overtemperature on Channel 01"


def test_inject_status_fires_on_event_and_is_pollable() -> None:
    received: list[Response] = []
    fired = threading.Event()

    def on_event(resp: Response) -> None:
        received.append(resp)
        fired.set()

    with FakeLucon() as fake:
        host, port = fake.address
        with Transport(host, port, on_event=on_event) as transport:
            fake.inject_status("RUNNING...")
            assert fired.wait(2.0), "on_event callback did not fire"
            polled = transport.poll_events(timeout=2.0)

    assert len(received) == 1
    assert received[0].kind is codec.ResponseKind.STATUS
    assert received[0].message == "RUNNING..."
    assert polled is not None
    assert polled.kind is codec.ResponseKind.STATUS
    assert polled.message == "RUNNING..."


def test_poll_events_returns_none_when_empty() -> None:
    with FakeLucon() as fake:
        host, port = fake.address
        with Transport(host, port) as transport:
            assert transport.poll_events() is None
            assert transport.poll_events(timeout=0.1) is None


def test_bad_callback_does_not_kill_rx_thread() -> None:
    # A raising callback must be guarded so the RX thread survives and later
    # solicited replies still come through.
    def boom(resp: Response) -> None:
        raise RuntimeError("callback explodes")

    with FakeLucon() as fake:
        host, port = fake.address
        with Transport(host, port, on_event=boom) as transport:
            fake.inject_status("RUNNING...")
            # Give the RX thread a moment to process the (raising) callback.
            assert transport.poll_events(timeout=2.0) is not None
            # The RX thread is still alive: a normal query still resolves.
            fake.set_read(1, "T", "42")
            resp = transport.query(codec.encode_read(1, "T"))
    assert resp.values == ("42",)


# --- context manager ------------------------------------------------------


def test_context_manager_opens_and_closes() -> None:
    with FakeLucon() as fake:
        host, port = fake.address
        with Transport(host, port) as transport:
            assert transport.is_open is True
        assert transport.is_open is False


def test_close_is_idempotent() -> None:
    with FakeLucon() as fake:
        host, port = fake.address
        transport = Transport(host, port)
        transport.open()
        transport.close()
        transport.close()  # second close must not raise
        assert transport.is_open is False


# --- probe() --------------------------------------------------------------


def test_probe_true_when_device_replies() -> None:
    with FakeLucon() as fake:
        host, port = fake.address
        assert probe(host, port, timeout=1.0) is True


def test_probe_false_when_no_device() -> None:
    port = _dead_port()
    assert probe("127.0.0.1", port, timeout=0.2) is False


# --- unmatched solicited-looking datagram ---------------------------------


def test_unsolicited_read_reply_with_no_waiter_is_routed_as_event() -> None:
    # A READ_REPLY-shaped datagram arriving with no in-flight request is not a
    # reply to anything; it must surface via on_event + poll_events, never get
    # silently dropped.
    received: list[Response] = []
    fired = threading.Event()

    def on_event(resp: Response) -> None:
        received.append(resp)
        fired.set()

    with _SilentAfterHandshake() as server:
        host, port = server.address
        with Transport(host, port, on_event=on_event) as transport:
            # The handshake registered the transport as the server's remote;
            # push an unsolicited READ_REPLY back to it, as a real device would.
            unsolicited = (
                "R02CM" + codec.DELIMITER + "4" + codec.DELIMITER + codec.TERMINATOR
            ).encode("ascii")
            server.inject(unsolicited)
            assert fired.wait(2.0), "on_event did not fire for unmatched reply"
            polled = transport.poll_events(timeout=2.0)

    assert len(received) == 1
    assert received[0].kind is codec.ResponseKind.READ_REPLY
    assert polled is not None
    assert polled.echo == "R02CM"


# --- slow-but-alive device: full per-attempt timeout ----------------------


def test_slow_reply_within_timeout_succeeds_in_one_attempt() -> None:
    # A device that answers after a delay shorter than the per-attempt timeout
    # must resolve on the first attempt (no retransmit), proving the wait honors
    # the full timeout rather than treating an early wakeup as a timeout.
    reply = (
        "R01T" + codec.DELIMITER + "31" + codec.DELIMITER + codec.TERMINATOR
    ).encode("ascii")
    with _DelayedReply(delay=0.4, reply=reply) as server:
        host, port = server.address
        with Transport(host, port, timeout=1.0, retries=2) as transport:
            resp = transport.query(codec.encode_read(1, "T"))
    assert resp.values == ("31",)
    # Exactly one post-handshake datagram: the wait was not cut short into a
    # spurious retransmit.
    assert server.post_handshake_datagrams == 1


def test_slow_reply_after_retransmit_leaves_no_phantom_event() -> None:
    # If the reply is slow enough to trigger a retransmit, the device's second
    # (duplicate) reply must NOT be surfaced as a bogus event. The call still
    # succeeds and poll_events stays empty.
    reply = (
        "R01T" + codec.DELIMITER + "31" + codec.DELIMITER + codec.TERMINATOR
    ).encode("ascii")
    with _DelayedReply(delay=0.25, reply=reply) as server:
        host, port = server.address
        with Transport(host, port, timeout=0.15, retries=2) as transport:
            resp = transport.query(codec.encode_read(1, "T"))
            # Let the duplicate (and any retransmit's reply) arrive and route.
            time.sleep(0.5)
            phantom = transport.poll_events()
    assert resp.values == ("31",)
    assert phantom is None, f"duplicate reply leaked as an event: {phantom!r}"


# --- overtemp during an in-flight request ---------------------------------


def test_unsolicited_overtemp_during_request_still_reaches_on_error() -> None:
    # An overtemp :E arriving while a normal request is in flight must never be
    # silently swallowed: even if it is consumed as the reply, on_error/
    # poll_events must still see it (safety-critical fault notification).
    errors: list[Response] = []
    fired = threading.Event()

    def on_error(resp: Response) -> None:
        errors.append(resp)
        fired.set()

    overtemp = (
        ":E Overtemperature on Channel 01" + codec.DELIMITER + codec.TERMINATOR
    ).encode("ascii")
    # The server delays the real reply, then injects an overtemp while the
    # READ is in flight; the transport must surface the overtemp to on_error.
    with _DelayedReply(delay=0.3, reply=overtemp) as server:
        host, port = server.address
        with Transport(
            host, port, timeout=2.0, retries=0, on_error=on_error
        ) as transport:
            with pytest.raises(LuconCommandError):
                transport.query(codec.encode_read(1, "T"))
            assert fired.wait(2.0), "overtemp during request never reached on_error"
    assert errors
    assert errors[0].message == "Overtemperature on Channel 01"


# --- close() during an in-flight request fails fast -----------------------


def test_close_during_in_flight_request_fails_fast() -> None:
    # A close() concurrent with a blocked _exchange must wake it immediately and
    # raise a connection error, not wait out the full timeout/retries.
    with _SilentAfterHandshake() as server:
        host, port = server.address
        transport = Transport(host, port, timeout=5.0, retries=3)
        transport.open()
        result: list[object] = []

        def do_query() -> None:
            try:
                transport.query(codec.encode_read(1, "T"))
                result.append("ok")
            except Exception as exc:  # noqa: BLE001 - capturing for assertion
                result.append(exc)

        worker = threading.Thread(target=do_query)
        worker.start()
        time.sleep(0.3)  # let the query block in the wait
        start = time.monotonic()
        transport.close()
        worker.join(timeout=2.0)
        elapsed = time.monotonic() - start

    assert not worker.is_alive(), "query did not return promptly after close()"
    assert result and isinstance(result[0], LuconConnectionError)
    # Far less than the would-be 5s * 4 attempts if close() did not wake it.
    assert elapsed < 2.0
