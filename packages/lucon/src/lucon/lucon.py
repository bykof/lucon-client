"""The top-level connection (:class:`Lucon`) — a LUCON *Chain*.

A :class:`Lucon` is the object you connect to: the master Controller plus its
0–23 slaves, reached as a unit through the master's single Ethernet interface
(CONTEXT.md "Chain"; ADR-0002). It owns:

* the one :class:`~lucon.transport.Transport` (socket + RX thread + callbacks),
* **every general ``00`` command** — device identity, network config,
  save/reset/restart — which physically address the master only, and
* the :class:`~lucon.controller.Controller` / :class:`~lucon.channel.Channel`
  tree, auto-built from ``R00RT`` (the online-channel list) on :meth:`open`.

It offers a global ``channel(1..96)`` shortcut over the nested tree and an
escape hatch (:meth:`send` / :meth:`query`) for raw codec-built commands.
"""

from __future__ import annotations

from types import TracebackType
from typing import Callable

from lucon import codec
from lucon.channel import Channel
from lucon.codec import Response
from lucon.controller import Controller
from lucon.exceptions import LuconError
from lucon.transport import Transport

# General save / factory-reset scopes (0 = channel, 1 = general, 2 = all).
_VALID_SCOPES = frozenset({0, 1, 2})


class Lucon:
    """A LUCON chain reached through its master's Ethernet interface.

    Use as a context manager (``with Lucon(host) as lucon:``) or call
    :meth:`open` / :meth:`close` explicitly. The Controller/Channel tree is
    built on :meth:`open` from the device's ``R00RT`` online-channel list.
    """

    def __init__(
        self,
        host: str,
        port: int = 50000,
        *,
        timeout: float = 1.0,
        retries: int = 2,
        current_tenths: bool = False,
        on_error: Callable[[Response], None] | None = None,
        on_event: Callable[[Response], None] | None = None,
    ) -> None:
        # current_tenths threads into Channel current READS via
        # codec.parse_current(text, tenths=...). DEFAULT False (decimal mA).
        # We do NOT auto-probe: setting a current at connect could energize an
        # output, so the interpretation is an explicit, safe flag instead.
        # Confirmed on hardware (fw 0.5.0): driven currents read back as decimal
        # mA, so False is correct; the flag stays as a hedge (CONTEXT.md item #1).
        self._current_tenths = current_tenths
        self._transport = Transport(
            host,
            port,
            timeout=timeout,
            retries=retries,
            on_error=on_error,
            on_event=on_event,
        )
        self._controllers: dict[int, Controller] = {}

    # --- lifecycle ------------------------------------------------------

    @property
    def is_open(self) -> bool:
        """True between a successful :meth:`open` and the next :meth:`close`."""
        return self._transport.is_open

    @property
    def current_tenths(self) -> bool:
        """How sub-45 mA current READS are interpreted (CONTEXT.md open item #1).

        ``True`` reads an integer-tenths token (``"354"`` -> 35.4 mA); ``False``
        (default) reads decimal mA. Settable at runtime — it only reinterprets
        READ values client-side and never touches the device, so flipping it is
        safe (e.g. to confirm the real sub-45 mA format against hardware).
        """
        return self._current_tenths

    @current_tenths.setter
    def current_tenths(self, value: bool) -> None:
        self._current_tenths = bool(value)

    def open(self) -> None:
        """Open the transport (handshake) and build the Controller/Channel tree."""
        self._transport.open()
        self._build_tree()

    def close(self) -> None:
        """Close the transport. Idempotent."""
        self._transport.close()

    def __enter__(self) -> Lucon:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # --- tree building --------------------------------------------------

    def _build_tree(self) -> None:
        """Discover online channels via ``R00RT`` and group them by offset.

        ``R00RT`` returns the global channel numbers that are online (e.g.
        ``"Online: 01, 02, 03, 04"``). Slave presence is inferred from that
        list: each block of four channels (``offset * 4 + 1..4``) maps to one
        Controller offset.
        """
        reply = self._transport.query(codec.encode_read(0, "RT"))
        online = _parse_online_channels(reply)
        offsets = sorted({(num - 1) // 4 for num in online})
        self._controllers = {offset: Controller(self, offset) for offset in offsets}

    # --- topology accessors ---------------------------------------------

    @property
    def offsets(self) -> list[int]:
        """Sorted Controller offsets discovered in the chain."""
        return sorted(self._controllers)

    @property
    def controllers(self) -> list[Controller]:
        """All :class:`~lucon.controller.Controller`s, ordered by offset."""
        return [self._controllers[off] for off in self.offsets]

    def controller(self, offset: int) -> Controller:
        """Return the :class:`~lucon.controller.Controller` at ``offset``."""
        try:
            return self._controllers[offset]
        except KeyError:
            raise LuconError(f"no Controller at offset {offset}") from None

    def channel(self, channel_num: int) -> Channel:
        """Global channel shortcut: return the :class:`~lucon.channel.Channel` ``1..96``."""
        if not 1 <= channel_num <= 96:
            raise LuconError(f"channel number must be 1-96, got {channel_num}")
        offset, local = divmod(channel_num - 1, 4)
        controller = self._controllers.get(offset)
        if controller is None:
            raise LuconError(f"channel {channel_num} is not online")
        return controller.channel(local + 1)

    # --- general (00) identity / status reads ---------------------------

    def _read_general(self, cmd: str) -> str:
        values = self._transport.query(codec.encode_read(0, cmd)).values
        if not values:
            raise LuconError(f"empty reply to general read {cmd!r}")
        return values[0]

    def firmware(self) -> str:
        """Master firmware string (``R00F``). Master-only (CONTEXT.md)."""
        return self._read_general("F")

    def serial(self) -> str:
        """Master serial number (``R00SN``). Master-only."""
        return self._read_general("SN")

    def mac(self) -> str:
        """Master MAC address (``R00MAC``). Master-only."""
        return self._read_general("MAC")

    def ip(self) -> str:
        """Master IP address (``R00IP``)."""
        return self._read_general("IP")

    def subnet(self) -> str:
        """Master subnet mask (``R00SM``)."""
        return self._read_general("SM")

    def udp_port(self) -> int:
        """Master UDP command port (``R00UDP``)."""
        return int(self._read_general("UDP"))

    def bootloader(self) -> str:
        """Master bootloader version (``R00BLV``)."""
        return self._read_general("BLV")

    def pcb_revision_control(self) -> str:
        """Control-PCB revision (``R00RCP``). Master-only."""
        return self._read_general("RCP")

    def pcb_revision_power(self) -> str:
        """Power-PCB revision (``R00RPP``). Master-only."""
        return self._read_general("RPP")

    def supply_voltage_mv(self) -> int:
        """Master supply voltage in mV (``R00USU``)."""
        return int(self._read_general("USU"))

    def controller_offset(self) -> int:
        """Master's own Controller offset (``R00CO``)."""
        return int(self._read_general("CO"))

    def error_buffer(self) -> str:
        """Master error buffer text (``R00M``)."""
        return self._read_general("M")

    def is_persisted(self) -> bool:
        """True if general Permanent memory matches Temporary (``R00EQ`` == 1)."""
        return self._read_general("EQ").strip() == "1"

    # --- general (00) setters / actions ---------------------------------

    def set_controller_offset(self, offset: int) -> None:
        """Set the master's Controller offset (``S00CO``)."""
        self._transport.send(codec.encode_set(0, "CO", str(offset)))

    def set_ip(self, ip: str) -> None:
        """Set the master IP address (``S00IP``)."""
        self._transport.send(codec.encode_set(0, "IP", ip))

    def set_subnet(self, subnet: str) -> None:
        """Set the master subnet mask (``S00SM``)."""
        self._transport.send(codec.encode_set(0, "SM", subnet))

    def set_ip_checked(self, ip: str, serial: str) -> None:
        """Set the IP only on the unit whose serial matches (``S00SIP|ip|serial``).

        Lets a host address a specific unit by serial before it has a known IP.
        """
        self._transport.send(codec.encode_set(0, "SIP", ip, serial))

    def save(self, scope: int) -> None:
        """Promote Temporary memory to Permanent memory (``S00S|<scope>``).

        Scope values, per the manual (section 7.4.2.2):

        * ``0`` — *general* parameters of this (master) unit only;
        * ``1`` — general **and** channel-specific parameters of this unit;
        * ``2`` — channel-specific parameters of this **and all** connected
          units (master/slave). Note: scope 2 does **not** save general params.

        Validated client-side; an out-of-range scope raises before the wire.
        """
        if scope not in _VALID_SCOPES:
            raise ValueError(
                f"save scope must be in {sorted(_VALID_SCOPES)}, got {scope}"
            )
        self._transport.send(codec.encode_set(0, "S", str(scope)))

    def save_general(self) -> None:
        """Save this unit's *general* parameters to Permanent memory (``S00S|0``)."""
        self.save(0)

    def save_all(self) -> None:
        """Save this unit's general **and** channel-specific params (``S00S|1``).

        For a lone unit this persists everything. To persist channel params
        across an entire master/slave chain, use :meth:`save` with scope ``2``.
        """
        self.save(1)

    def factory_reset(self, scope: int) -> None:
        """Factory-reset to defaults (``S00FR|<scope>``).

        Scope mirrors :meth:`save` (manual section 7.4.2.2): ``0`` = general of
        this unit, ``1`` = general + channel of this unit, ``2`` = channel of
        this and all connected units. Validated client-side; an out-of-range
        scope raises before the wire.
        """
        if scope not in _VALID_SCOPES:
            raise ValueError(
                f"factory_reset scope must be in {sorted(_VALID_SCOPES)}, got {scope}"
            )
        self._transport.send(codec.encode_set(0, "FR", str(scope)))

    def save_and_restart(self, scope: int) -> None:
        """Save (per :meth:`save` scope) then restart (``S00SR|<scope>``).

        Same scope meanings as :meth:`save`; scope ``2`` also restarts all
        connected units. Validated client-side.
        """
        if scope not in _VALID_SCOPES:
            raise ValueError(
                f"save_and_restart scope must be in {sorted(_VALID_SCOPES)}, got {scope}"
            )
        self._transport.send(codec.encode_set(0, "SR", str(scope)))

    def restart(self) -> None:
        """Restart the chain (``S00R``, no values). All connected slaves restart too."""
        self._transport.send(codec.encode_set(0, "R"))

    # --- escape hatch ---------------------------------------------------

    def send(self, raw: bytes) -> Response:
        """Send a raw, codec-built SET command and return its ack."""
        return self._transport.send(raw)

    def query(self, raw: bytes) -> Response:
        """Send a raw, codec-built READ command and return its reply."""
        return self._transport.query(raw)

    def poll_events(self, timeout: float | None = None) -> Response | None:
        """Pop the next unsolicited ``:E``/``:S`` notice, or ``None`` if none."""
        return self._transport.poll_events(timeout)


def _parse_online_channels(reply: Response) -> list[int]:
    """Extract online global channel numbers from an ``R00RT`` reply.

    The device reports them as a comma-separated list, optionally prefixed with
    ``Online:`` (e.g. ``"Online: 01, 02, 03, 04"``). We scan every value token
    for digit runs so the parse is robust to the exact framing.
    """
    numbers: list[int] = []
    for token in reply.values:
        for part in token.replace("Online:", " ").replace(",", " ").split():
            if part.isdigit():
                numbers.append(int(part))
    return numbers
