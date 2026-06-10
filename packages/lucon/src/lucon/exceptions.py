"""Exception hierarchy for the LUCON client.

Lean and flat: a single base (:class:`LuconError`) with four leaves. The device
sends error text in a firmware/locale-dependent form, so we never parse ``:E``
messages into structured fields — the original wording and bytes are preserved
on the exception for the caller to inspect or log.
"""

from __future__ import annotations


class LuconError(Exception):
    """Base class for every error raised by this library."""


class LuconCommandError(LuconError):
    """The device rejected a command with a ``:E`` error notice.

    Carries the device's verbatim ``message``, the ``command`` bytes that
    provoked it (when known), and the full ``raw`` datagram.
    """

    def __init__(
        self,
        message: str,
        *,
        command: bytes | None = None,
        raw: bytes | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.command = command
        self.raw = raw


class LuconTimeoutError(LuconError):
    """No reply arrived within the configured timeout, after all retries."""


class LuconConnectionError(LuconError):
    """The transport could not reach the device (socket or handshake failure)."""


class LuconProtocolError(LuconError):
    """A datagram could not be parsed as a valid device response."""

    def __init__(self, message: str, *, raw: bytes | None = None) -> None:
        super().__init__(message)
        self.raw = raw
