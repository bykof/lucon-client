"""Synchronous UDP transport for the LUCON 4C-20A-V (ADR-0001).

The device speaks an ASCII command protocol over **UDP**. It is not a clean
request/response channel: besides solicited replies (an echo terminated by
``>``) it emits **unsolicited** datagrams on the same socket — an
overtemperature ``:E`` during operation and a ``:S RUNNING`` notice after boot
— and it only sends these to the *last remote station* that contacted it.

:class:`Transport` therefore exposes a **synchronous** API while running **one
daemon RX thread** that drains the socket continuously and demultiplexes:

* a single in-flight request (serialized by a lock) gets its reply handed back
  to the calling thread through a thread-safe slot;
* unsolicited ``:S``/``:E`` are routed to the ``on_event``/``on_error``
  callbacks (fired on the RX thread) and pushed to a fallback queue drained by
  :meth:`Transport.poll_events`.

:meth:`Transport.open` performs the mandatory ``R00F`` handshake to register as
the remote station and verify reachability.
"""

from __future__ import annotations

import logging
import queue
import socket
import threading
import time
from types import TracebackType
from typing import Callable

from lucon import codec
from lucon.codec import Response, ResponseKind
from lucon.exceptions import (
    LuconCommandError,
    LuconConnectionError,
    LuconTimeoutError,
)

__all__ = ["Transport", "probe"]

_LOG = logging.getLogger("lucon")

# Default device UDP command port.
_DEFAULT_PORT = 50000

# The handshake command used to register as the remote station and to verify
# reachability: a READ of the master's firmware (general channel 00).
_HANDSHAKE = codec.encode_read(0, "F")

# How often the RX thread wakes to check whether it has been asked to stop.
_POLL_INTERVAL_S = 0.1

# Receive buffer; device datagrams are short ASCII lines.
_RECV_BUFSIZE = 4096


def _expected_echo(command: bytes) -> str:
    """The echo a solicited reply must carry: the command minus its delimiter."""
    text = command.decode("ascii")
    if text.endswith(codec.DELIMITER):
        text = text[: -len(codec.DELIMITER)]
    return text


class Transport:
    """A synchronous, thread-safe UDP transport to a LUCON master Controller."""

    def __init__(
        self,
        host: str,
        port: int = _DEFAULT_PORT,
        *,
        timeout: float = 1.0,
        retries: int = 2,
        on_error: Callable[[Response], None] | None = None,
        on_event: Callable[[Response], None] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        self._retries = retries
        self._on_error = on_error
        self._on_event = on_event

        self._sock: socket.socket | None = None
        self._rx_thread: threading.Thread | None = None
        self._stop = threading.Event()
        # Set only after the mandatory handshake in open() succeeds; cleared by
        # close(). Distinct from "a socket is bound", which is true earlier.
        self._opened = False

        # Serializes send/query so at most one request is in flight.
        self._io_lock = threading.Lock()
        # Guards the in-flight slot and is the condition the caller waits on.
        self._reply_cond = threading.Condition()
        # The echo the in-flight request expects in its reply, or None when idle.
        self._expected_echo: str | None = None
        # The reply handed back by the RX thread for the in-flight request.
        self._reply_slot: Response | None = None
        # Echo of the most recently satisfied request. A duplicate reply from a
        # slow device (after a retransmit) carries this echo and arrives with no
        # waiter; it must be dropped, not surfaced as a phantom event.
        self._recent_echo: str | None = None

        # Fallback queue of unsolicited notices for poll_events().
        self._events: queue.Queue[Response] = queue.Queue()

        # Guards lifecycle (open/close) against itself.
        self._lifecycle_lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        """True between a successful :meth:`open` and the next :meth:`close`.

        Reflects a *successfully* opened transport: the flag is set only after
        the mandatory ``R00F`` handshake returns, not merely when a socket has
        been bound, so it never reports True for a connection whose handshake is
        still in flight or about to be torn down.
        """
        return self._opened

    def open(self) -> None:
        """Open a connected UDP socket, start the RX thread, then handshake.

        Performs the mandatory ``R00F`` handshake to register this client as
        the device's remote station and to verify reachability. If the device
        does not answer within the configured timeout/retries the socket is
        torn down and :class:`~lucon.exceptions.LuconConnectionError` is raised.
        """
        with self._lifecycle_lock:
            if self._sock is not None:
                return
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.connect((self._host, self._port))
            except OSError as exc:
                sock.close()
                raise LuconConnectionError(f"cannot open socket to {self._host}:{self._port}: {exc}")
            sock.settimeout(_POLL_INTERVAL_S)
            self._sock = sock
            self._stop.clear()
            self._rx_thread = threading.Thread(
                target=self._rx_loop, name="lucon-rx", daemon=True
            )
            self._rx_thread.start()

        try:
            self._exchange(_HANDSHAKE)
        except LuconTimeoutError as exc:
            self.close()
            raise LuconConnectionError(
                f"device at {self._host}:{self._port} did not answer handshake: {exc}"
            )
        except Exception:
            self.close()
            raise
        self._opened = True

    def close(self) -> None:
        """Stop the RX thread and close the socket. Idempotent.

        Wakes any in-flight :meth:`_exchange` so a request blocked waiting for a
        reply fails fast instead of waiting out the full timeout/retries (the RX
        thread is stopping, so the reply can no longer arrive).
        """
        with self._lifecycle_lock:
            sock, self._sock = self._sock, None
            thread, self._rx_thread = self._rx_thread, None
            self._opened = False
            self._stop.set()
        # Wake any caller blocked in _exchange's wait so it can observe the
        # torn-down socket and fail fast.
        with self._reply_cond:
            self._reply_cond.notify_all()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        if sock is not None:
            sock.close()

    def __enter__(self) -> Transport:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # --- request/reply --------------------------------------------------

    def _exchange(self, command: bytes) -> Response:
        """Transmit ``command`` and return its matching reply.

        Serialized so only one request is in flight. Retransmits up to
        ``retries`` extra times on timeout (all commands are idempotent), and
        raises :class:`~lucon.exceptions.LuconTimeoutError` if no reply ever
        matches, or :class:`~lucon.exceptions.LuconCommandError` if the device
        answers the request with a ``:E`` error.
        """
        sock = self._sock
        if sock is None:
            raise LuconConnectionError("transport is not open")
        echo = _expected_echo(command)
        with self._io_lock:
            with self._reply_cond:
                self._expected_echo = echo
                self._reply_slot = None
            try:
                reply: Response | None = None
                for _attempt in range(self._retries + 1):
                    # A concurrent close() may have torn the socket down between
                    # attempts; fail fast rather than send on a dead fd.
                    if self._sock is None or self._stop.is_set():
                        raise LuconConnectionError("transport closed during request")
                    try:
                        sock.send(command)
                    except OSError as exc:
                        raise LuconConnectionError(f"send failed: {exc}")
                    # Wait the FULL per-attempt timeout, looping to absorb
                    # spurious/early wakeups (Condition.wait may return early).
                    deadline = time.monotonic() + self._timeout
                    with self._reply_cond:
                        while self._reply_slot is None:
                            if self._sock is None or self._stop.is_set():
                                raise LuconConnectionError(
                                    "transport closed during request"
                                )
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                break
                            self._reply_cond.wait(remaining)
                        reply = self._reply_slot
                        self._reply_slot = None
                    if reply is not None:
                        break
                if reply is None:
                    raise LuconTimeoutError(
                        f"no reply to {echo!r} after {self._retries + 1} attempts"
                    )
            finally:
                with self._reply_cond:
                    self._expected_echo = None
                    self._reply_slot = None
                    # Remember the echo we just (or last) waited on so a late
                    # duplicate of this reply is dropped rather than treated as
                    # an event.
                    self._recent_echo = echo

        if reply.kind is ResponseKind.ERROR:
            raise LuconCommandError(
                reply.message or "",
                command=command,
                raw=reply.raw,
            )
        return reply

    # --- RX thread ------------------------------------------------------

    def _rx_loop(self) -> None:
        """Drain the socket and demux replies vs. unsolicited notices."""
        sock = self._sock
        if sock is None:
            return
        while not self._stop.is_set():
            try:
                data = sock.recv(_RECV_BUFSIZE)
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                break
            try:
                response = codec.decode(data)
            except Exception:
                _LOG.warning("dropping undecodable datagram: %r", data)
                continue
            self._route(response)

    def _route(self, response: Response) -> None:
        """Route a decoded datagram to the waiting caller or to callbacks."""
        if response.kind is ResponseKind.STATUS:
            # :S is always unsolicited (e.g. boot RUNNING), never a reply.
            self._dispatch_event(response)
            return

        if response.kind is ResponseKind.ERROR:
            with self._reply_cond:
                if self._expected_echo is not None and self._reply_slot is None:
                    # A :E does not echo, so an error while a request is in
                    # flight is handed back as that request's reply.
                    self._reply_slot = response
                    self._reply_cond.notify_all()
            # ALWAYS also surface the :E to on_error/poll_events, even when it
            # was consumed as a reply. A :E text is firmware/locale-dependent
            # (CONTEXT.md) so we cannot reliably tell a solicited rejection from
            # an unsolicited overtemp that merely *collided* with an in-flight
            # request; dispatching unconditionally guarantees a safety-critical
            # fault is never silently swallowed.
            self._dispatch_error(response)
            return

        # SET_ACK / READ_REPLY: match the in-flight expected echo.
        with self._reply_cond:
            if (
                self._expected_echo is not None
                and response.echo == self._expected_echo
                and self._reply_slot is None
            ):
                self._reply_slot = response
                self._reply_cond.notify_all()
                return
            waiting = self._expected_echo is not None
            recent_echo = self._recent_echo
        if waiting:
            # Echo mismatch while waiting: a stale/late reply — ignore.
            _LOG.debug("ignoring stale reply %r (awaiting %r)", response.echo, self._expected_echo)
        elif response.echo is not None and response.echo == recent_echo:
            # A duplicate of the just-completed request (a slow device answered
            # both the original and the retransmit): drop it, not an event.
            _LOG.debug("dropping duplicate reply %r for completed request", response.echo)
        else:
            # Solicited-looking datagram with no waiter: treat as an event.
            self._dispatch_event(response)

    def _dispatch_event(self, response: Response) -> None:
        self._events.put(response)
        if self._on_event is not None:
            try:
                self._on_event(response)
            except Exception:
                _LOG.exception("on_event callback raised")

    def _dispatch_error(self, response: Response) -> None:
        self._events.put(response)
        if self._on_error is not None:
            try:
                self._on_error(response)
            except Exception:
                _LOG.exception("on_error callback raised")

    # --- public command API ---------------------------------------------

    def send(self, command: bytes) -> Response:
        """Transmit a SET command and return its :class:`Response` SET ack."""
        return self._exchange(command)

    def query(self, command: bytes) -> Response:
        """Transmit a READ command and return its :class:`Response` reply."""
        return self._exchange(command)

    def poll_events(self, timeout: float | None = None) -> Response | None:
        """Pop the next unsolicited notice, or ``None`` if none arrives in time.

        With ``timeout=None`` returns immediately (non-blocking); otherwise
        blocks up to ``timeout`` seconds for a notice.
        """
        try:
            if timeout is None:
                return self._events.get_nowait()
            return self._events.get(timeout=timeout)
        except queue.Empty:
            return None


def probe(host: str, port: int = _DEFAULT_PORT, timeout: float = 1.0) -> bool:
    """Best-effort reachability check via the ``R00F`` handshake.

    Returns ``True`` if the device replies within ``timeout`` (after the
    transport's retries), ``False`` otherwise. Never raises for an unreachable
    device.
    """
    transport = Transport(host, port, timeout=timeout)
    try:
        transport.open()
    except LuconConnectionError:
        return False
    transport.close()
    return True
