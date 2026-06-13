"""In-process UDP simulator of a LUCON master Controller.

:class:`FakeLucon` binds a real UDP socket on loopback and serves the LUCON
ASCII protocol from a daemon background thread, so the transport and domain
layers can be tested against the genuine socket path rather than a mock. It
speaks the wire format via :mod:`lucon.codec` (``parse_command`` to interpret
requests; raw byte replies terminated by ``>``).

It is a *testing* helper, not part of the device-facing client surface.
"""

from __future__ import annotations

import logging
import socket
import threading
from types import TracebackType
from typing import Self

from lucon import codec
from lucon.exceptions import LuconProtocolError

_LOG = logging.getLogger("lucon.testing")

# How often the server thread wakes to check whether it has been asked to stop.
_POLL_INTERVAL_S = 0.1

# Maps a channel READ mnemonic to the SET mnemonic whose last-written value it
# naturally reads back, so a script-free ``SET`` then ``READ`` round-trips. For
# example ``R01CA`` (continuous current flow) reflects the last ``S01MC``.
_READ_FROM_SET: dict[str, str] = {
    "CA": "MC",  # continuous current flow <- continuous current
    "PC": "MDU",  # pulse current <- pulse (|mA|delay|dur), value 0
    "SC": "MT",  # switch current <- switch current (also a SET mnemonic itself)
    "L": "L",
    "LP": "LP",
    "V": "V",
    "I": "I",
    "ST": "ST",
    "O": "O",
    "OTE": "OTE",
    "OTS": "OTS",
    "OTT": "OTT",
    "OTD": "OTD",
    "OTL": "OTL",
}


def _notice(prefix: str, message: str) -> bytes:
    """Frame an unsolicited/error notice: ``<prefix> <message><CRLF>>``.

    Encodes with ``errors="replace"`` (mirroring the decode side) so a
    locale-dependent :E message with umlauts — explicitly flagged as plausible
    in CONTEXT.md — never raises ``UnicodeEncodeError``.
    """
    return (f"{prefix} {message}" + codec.DELIMITER + codec.TERMINATOR).encode(
        "ascii", errors="replace"
    )


# Built-in defaults for channel READs the domain layer expects to "just work".
_CHANNEL_READ_DEFAULTS: dict[str, str] = {
    "T": "30",  # temperature, deg C
    "CM": "2",  # operating mode: 2 = continuous
    "CA": "0",  # current flow, mA
    "PC": "0",
    "SC": "0",
}


class FakeLucon:
    """A protocol-speaking UDP fake of a LUCON master, for use in tests.

    Parameters
    ----------
    online_channels:
        Global channel numbers reported online by ``R00RT`` (default
        ``{1, 2, 3, 4}``).
    firmware, serial, mac:
        Identity strings reported by ``R00F`` / ``R00SN`` / ``R00MAC``.
    """

    def __init__(
        self,
        *,
        online_channels: set[int] | None = None,
        firmware: str = "LUCON 4C-20A-V v1.0",
        serial: str = "FAKE-SERIAL-0001",
        mac: str = "00:11:22:33:44:55",
    ) -> None:
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._address: tuple[str, int] | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        # The last remote station (address) that sent us a datagram; injections
        # target it, mirroring the real device which only replies to the last
        # sender.
        self._remote: tuple[str, int] | None = None

        self._online_channels: set[int] = (
            {1, 2, 3, 4} if online_channels is None else set(online_channels)
        )
        self._firmware = firmware
        self._serial = serial
        self._mac = mac

        # Temporary memory: last SET value(s), keyed by (channel, cmd).
        self._memory: dict[tuple[int, str], tuple[str, ...]] = {}
        # Explicitly scripted READ replies, keyed by (channel, cmd).
        self._scripted_reads: dict[tuple[int, str], str] = {}
        # Per-command one-shot failure messages, keyed by (verb, channel, cmd).
        self._fail_next: dict[tuple[str, int, str], str] = {}

    @property
    def address(self) -> tuple[str, int]:
        """The bound ``(host, port)``; only valid once started."""
        if self._address is None:
            raise RuntimeError("FakeLucon is not started")
        return self._address

    def start(self) -> tuple[str, int]:
        """Bind the socket, launch the server thread, return ``(host, port)``.

        Idempotent: a second call on a running fake returns the same address.
        """
        with self._lock:
            if self._sock is None:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.bind(("127.0.0.1", 0))
                sock.settimeout(_POLL_INTERVAL_S)
                self._sock = sock
                self._address = sock.getsockname()
                self._stop.clear()
                self._thread = threading.Thread(target=self._serve, daemon=True)
                self._thread.start()
            assert self._address is not None
            return self._address

    def stop(self) -> None:
        """Shut down the server thread and close the socket. Idempotent."""
        with self._lock:
            sock, self._sock = self._sock, None
            thread, self._thread = self._thread, None
            self._address = None
            self._stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        if sock is not None:
            sock.close()

    def _serve(self) -> None:
        """Background loop: read datagrams and reply until asked to stop."""
        sock = self._sock
        assert sock is not None
        while not self._stop.is_set():
            try:
                data, remote = sock.recvfrom(4096)
            except (TimeoutError, OSError):
                continue
            try:
                self._remote = remote
                reply = self._handle(data)
                if reply is not None:
                    sock.sendto(reply, remote)
            except OSError:
                # Socket torn down mid-send during stop(): nothing to do.
                pass
            except Exception:
                # A single malformed datagram must never silently kill the RX
                # thread and leave the fake permanently unresponsive.
                _LOG.exception("FakeLucon dropping datagram that raised: %r", data)

    def set_read(self, channel: int, cmd: str, value: str) -> None:
        """Script the value the fake reports for a ``READ`` of ``(channel, cmd)``."""
        self._scripted_reads[(channel, cmd)] = value

    def fail_next(
        self, message: str, *, channel: int | None = None, cmd: str | None = None
    ) -> None:
        """Make the next matching command reply with ``:E <message>``.

        With no ``channel``/``cmd`` the very next command (SET or READ) fails;
        otherwise only the next command matching both filters fails. A failure
        is consumed once it fires.
        """
        verb = ""  # empty verb component => matches any verb
        key = (verb, -1 if channel is None else channel, "" if cmd is None else cmd)
        self._fail_next[key] = message

    def inject_error(self, message: str) -> None:
        """Send an unsolicited ``:E <message>`` to the last remote station.

        Raises :class:`RuntimeError` if no client has contacted the fake yet.
        """
        self._inject(":E", message)

    def inject_status(self, message: str) -> None:
        """Send an unsolicited ``:S <message>`` to the last remote station.

        Raises :class:`RuntimeError` if no client has contacted the fake yet.
        """
        self._inject(":S", message)

    def _inject(self, prefix: str, message: str) -> None:
        # Snapshot under the lock (mirroring stop()'s snapshot pattern) so a
        # concurrent stop() cannot null/close the socket between our None-check
        # and the send.
        with self._lock:
            sock = self._sock
            remote = self._remote
        if sock is None:
            raise RuntimeError("FakeLucon is not started")
        if remote is None:
            raise RuntimeError("no remote station yet; a client must send first")
        try:
            sock.sendto(_notice(prefix, message), remote)
        except OSError as exc:
            # stop() closed the snapshot's socket after we read it.
            raise RuntimeError("FakeLucon is not started") from exc

    def _handle(self, data: bytes) -> bytes | None:
        """Produce the reply bytes for a received request datagram."""
        try:
            command = codec.parse_command(data)
        except LuconProtocolError:
            # The real device answers a malformed/rejected command with an :E
            # error rather than going silent, so mirror that here.
            return _notice(":E", "malformed command")
        forced = self._take_failure(command)
        if forced is not None:
            return _notice(":E", forced)
        if command.verb == "S":
            return self._handle_set(data, command)
        return self._handle_read(command)

    def _take_failure(self, command: codec.Command) -> str | None:
        """Pop and return a queued failure message matching ``command``, if any."""
        for key in (
            ("", -1, ""),  # next command, unconditionally
            ("", command.channel, ""),  # next on this channel
            ("", -1, command.cmd),  # next of this cmd on any channel
            ("", command.channel, command.cmd),  # most specific
        ):
            if key in self._fail_next:
                return self._fail_next.pop(key)
        return None

    def _handle_set(self, data: bytes, command: codec.Command) -> bytes:
        """SET: store the value(s) in temporary memory, then echo the line + '>'."""
        self._memory[(command.channel, command.cmd)] = command.values
        echo = data.decode("ascii", errors="replace").rstrip("\r\n")
        return (echo + codec.DELIMITER + codec.TERMINATOR).encode("ascii")

    def _handle_read(self, command: codec.Command) -> bytes:
        """READ: ``R<cc><cmd><CRLF><value><CRLF>>`` with a resolved value."""
        echo = f"R{command.channel:02d}{command.cmd}"
        value = self._resolve_read(command.channel, command.cmd)
        body = echo + codec.DELIMITER + value + codec.DELIMITER + codec.TERMINATOR
        return body.encode("ascii")

    def _resolve_read(self, channel: int, cmd: str) -> str:
        """Resolve a READ value: scripted, then last SET, then a sensible default."""
        scripted = self._scripted_reads.get((channel, cmd))
        if scripted is not None:
            return scripted
        # An exact stored SET for this very mnemonic, or its natural counterpart.
        for source_cmd in (cmd, _READ_FROM_SET.get(cmd)):
            if source_cmd is None:
                continue
            stored = self._memory.get((channel, source_cmd))
            if stored:
                return stored[0]
        if channel == 0:
            return self._resolve_general_read(cmd)
        return _CHANNEL_READ_DEFAULTS.get(cmd, "0")

    def _resolve_general_read(self, cmd: str) -> str:
        """Defaults for general (channel 00) identity/status READs."""
        if cmd == "F":
            return self._firmware
        if cmd == "SN":
            return self._serial
        if cmd == "MAC":
            return self._mac
        if cmd == "RT":
            channels = ", ".join(f"{n:02d}" for n in sorted(self._online_channels))
            return f"Online: {channels}"
        if cmd in ("IP", "SM"):
            return "0.0.0.0"
        if cmd == "UDP":
            return "8000"
        if cmd == "BLV":
            return "1.0"
        if cmd == "CO":
            return "0"
        if cmd == "EQ":
            return "1"
        return ""

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()
