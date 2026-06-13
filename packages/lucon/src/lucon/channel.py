"""One LED output (:class:`Channel`) of a LUCON Controller.

A :class:`Channel` owns the full per-channel command set (``01``–``96``). Its
**wire address** is the positional global channel number
``offset * 4 + local_index`` (CONTEXT.md), zero-padded to two digits by the
codec. All I/O is funnelled through the owning :class:`~lucon.lucon.Lucon`'s
single :class:`~lucon.transport.Transport`, since the whole chain shares one
Ethernet interface.

**Memory model (CONTEXT.md).** Every setter writes only to *Temporary memory*;
it is lost on restart. :meth:`Channel.save` (``S<cc>S``) promotes the channel's
temporary parameters to *Permanent memory*. Reads always reflect Temporary
memory (the live working set).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lucon import codec
from lucon.codec import Response
from lucon.enums import Mode, OutputTriggerSource, OutputTriggerType, TriggerEdge
from lucon.exceptions import LuconError

if TYPE_CHECKING:
    from lucon.controller import Controller

# Client-side bounds, validated BEFORE touching the wire (the brief / manual).
_MAX_CONTINUOUS_MA = 3000.0  # continuous current & continuous current limit (3 A)
_MAX_HIGH_MA = 20000.0  # switch/pulse current & their limit (20 A)
_MIN_VOLTAGE_MV = 1000
_MAX_VOLTAGE_MV = 60000
_MIN_PULSE_TIME_US = 5
_MAX_PULSE_TIME_US = 59_000_000
_MIN_PULSE_DELAY_US = 3
_MAX_PULSE_DELAY_US = 59_000_000
_MIN_OUTPUT_LENGTH_US = 20
_MAX_OUTPUT_LENGTH_US = 1_000_000
_MIN_OUTPUT_DELAY_US = 0
_MAX_OUTPUT_DELAY_US = 1_000_000


class Channel:
    """One of the four LED outputs on a :class:`~lucon.controller.Controller`.

    Construct via the owning :class:`~lucon.lucon.Lucon` / Controller tree, not
    directly. ``local_index`` is 1–4; the wire address is :attr:`channel_num`.
    """

    def __init__(self, controller: Controller, local_index: int) -> None:
        if not 1 <= local_index <= 4:
            raise ValueError(f"local_index must be 1-4, got {local_index}")
        self._controller = controller
        self._local_index = local_index

    # --- identity / metadata -------------------------------------------

    @property
    def controller(self) -> Controller:
        """The :class:`~lucon.controller.Controller` this channel belongs to."""
        return self._controller

    @property
    def local_index(self) -> int:
        """This channel's 1–4 position within its Controller."""
        return self._local_index

    @property
    def channel_num(self) -> int:
        """The global wire address: ``offset * 4 + local_index`` (1–96)."""
        return self._controller.offset * 4 + self._local_index

    def __repr__(self) -> str:
        return (
            f"Channel(channel_num={self.channel_num}, local_index={self._local_index})"
        )

    # --- transport helpers ---------------------------------------------

    def _set(self, cmd: str, *values: str) -> Response:
        """Send a SET to this channel's wire address through the master."""
        return self._controller._lucon.send(
            codec.encode_set(self.channel_num, cmd, *values)
        )

    def _query(self, cmd: str, *values: str) -> Response:
        """Send a READ to this channel's wire address and return the reply."""
        return self._controller._lucon.query(
            codec.encode_read(self.channel_num, cmd, *values)
        )

    def _read_value(self, cmd: str) -> str:
        """READ ``cmd`` and return its first value token."""
        values = self._query(cmd).values
        if not values:
            raise LuconError(f"empty reply to channel {self.channel_num} read {cmd!r}")
        return values[0]

    # --- mode setters (Temporary memory) -------------------------------

    def set_continuous(self, ma: float) -> None:
        """Continuous mode: drive ``ma`` mA until stopped (``MC``, max 3 A).

        Writes Temporary memory only; call :meth:`save` to persist.
        """
        if not 0 <= ma <= _MAX_CONTINUOUS_MA:
            raise ValueError(
                f"continuous current must be 0-{_MAX_CONTINUOUS_MA} mA, got {ma}"
            )
        self._set("MC", codec.format_current(ma))

    def set_switch_current(self, ma: float) -> None:
        """Switch mode: on while the trigger is active (``MT``, max 20 A).

        Writes Temporary memory only; call :meth:`save` to persist.
        """
        if not 0 <= ma <= _MAX_HIGH_MA:
            raise ValueError(f"switch current must be 0-{_MAX_HIGH_MA} mA, got {ma}")
        self._set("MT", codec.format_current(ma))

    def set_pulse(self, ma: float, delay_us: int, duration_us: int) -> None:
        """Pulse mode: one ``duration_us`` pulse per trigger after ``delay_us``.

        Sends ``MDU|<current>|<delay_us>|<duration_us>`` — microseconds in the
        right slots (never the ms variant ``MD``). ``ma`` <= 20 A, ``delay_us``
        in 3..59_000_000, ``duration_us`` in 5..59_000_000. Writes Temporary
        memory only; call :meth:`save` to persist.
        """
        if not 0 <= ma <= _MAX_HIGH_MA:
            raise ValueError(f"pulse current must be 0-{_MAX_HIGH_MA} mA, got {ma}")
        if not _MIN_PULSE_DELAY_US <= delay_us <= _MAX_PULSE_DELAY_US:
            raise ValueError(
                f"pulse delay must be {_MIN_PULSE_DELAY_US}-{_MAX_PULSE_DELAY_US} us, got {delay_us}"
            )
        if not _MIN_PULSE_TIME_US <= duration_us <= _MAX_PULSE_TIME_US:
            raise ValueError(
                f"pulse duration must be {_MIN_PULSE_TIME_US}-{_MAX_PULSE_TIME_US} us, "
                f"got {duration_us}"
            )
        self._set("MDU", codec.format_current(ma), str(delay_us), str(duration_us))

    def set_none(self) -> None:
        """None / idle mode: disable output and trigger evaluation (``MN``).

        Holds stored parameters without driving anything. Writes Temporary
        memory only; call :meth:`save` to persist.
        """
        self._set("MN")

    # --- limits (Temporary memory) -------------------------------------

    def set_continuous_limit(self, ma: float) -> None:
        """Continuous current limit protecting the lighting (``L``, max 3 A)."""
        if not 0 <= ma <= _MAX_CONTINUOUS_MA:
            raise ValueError(
                f"continuous limit must be 0-{_MAX_CONTINUOUS_MA} mA, got {ma}"
            )
        self._set("L", codec.format_current(ma))

    def set_pulse_limit(self, ma: float) -> None:
        """Pulse/switch current limit protecting the lighting (``LP``, max 20 A)."""
        if not 0 <= ma <= _MAX_HIGH_MA:
            raise ValueError(
                f"pulse/switch limit must be 0-{_MAX_HIGH_MA} mA, got {ma}"
            )
        self._set("LP", codec.format_current(ma))

    def set_voltage_limit(self, mv: int) -> None:
        """Voltage limit protecting the Controller (``V``, 1000..60000 mV)."""
        if not _MIN_VOLTAGE_MV <= mv <= _MAX_VOLTAGE_MV:
            raise ValueError(
                f"voltage limit must be {_MIN_VOLTAGE_MV}-{_MAX_VOLTAGE_MV} mV, got {mv}"
            )
        self._set("V", str(mv))

    # --- trigger / switch config (Temporary memory) --------------------

    def set_switch_input_polarity(self, active_high: bool) -> None:
        """Switch-mode input polarity (``ST``): ``1`` active-high, ``0`` low."""
        self._set("ST", "1" if active_high else "0")

    def set_switch_current_value(self, ma: float) -> None:
        """Switch current value (``SC``), in mA."""
        if not 0 <= ma <= _MAX_HIGH_MA:
            raise ValueError(f"switch current must be 0-{_MAX_HIGH_MA} mA, got {ma}")
        self._set("SC", codec.format_current(ma))

    def set_pulse_input_polarity(self, edge: TriggerEdge) -> None:
        """Pulse-mode trigger input edge/polarity (``I``)."""
        self._set("I", edge.code)

    def set_output_enabled(self, enabled: bool) -> None:
        """Enable/disable the trigger output (``O``): ``1`` on, ``0`` off."""
        self._set("O", "1" if enabled else "0")

    def set_output_polarity(self, edge: TriggerEdge) -> None:
        """Trigger output polarity (``OTE``): RISING or FALLING only.

        BOTH is invalid for an output, which fires on a single edge.
        """
        if edge is TriggerEdge.BOTH:
            raise ValueError("output polarity must be RISING or FALLING, not BOTH")
        self._set("OTE", edge.code)

    def set_output_source(self, source: OutputTriggerSource) -> None:
        """Trigger output source (``OTS``): INPUT or LIGHTING."""
        self._set("OTS", source.code)

    def set_output_type(self, type_: OutputTriggerType) -> None:
        """Trigger output type (``OTT``): TIME_LIMITED or WHILE_LIT."""
        self._set("OTT", type_.code)

    def set_output_delay(self, us: int) -> None:
        """Trigger output delay (``OTD``), 0..1_000_000 us."""
        if not _MIN_OUTPUT_DELAY_US <= us <= _MAX_OUTPUT_DELAY_US:
            raise ValueError(
                f"output delay must be {_MIN_OUTPUT_DELAY_US}-{_MAX_OUTPUT_DELAY_US} us, got {us}"
            )
        self._set("OTD", str(us))

    def set_output_length(self, us: int) -> None:
        """Trigger output length (``OTL``), 20..1_000_000 us."""
        if not _MIN_OUTPUT_LENGTH_US <= us <= _MAX_OUTPUT_LENGTH_US:
            raise ValueError(
                f"output length must be {_MIN_OUTPUT_LENGTH_US}-{_MAX_OUTPUT_LENGTH_US} us, got {us}"
            )
        self._set("OTL", str(us))

    # --- reads ----------------------------------------------------------

    def temperature(self) -> float:
        """Channel temperature in degrees Celsius (``T``)."""
        return float(self._read_value("T"))

    def mode(self) -> Mode:
        """Current operating :class:`~lucon.enums.Mode` (``CM``)."""
        return Mode.from_wire(self._read_value("CM"))

    def _read_current(self, cmd: str) -> float:
        return codec.parse_current(
            self._read_value(cmd), tenths=self._controller._lucon._current_tenths
        )

    def pulse_current(self) -> float:
        """Configured pulse current in mA (``PC``)."""
        return self._read_current("PC")

    def switch_current(self) -> float:
        """Configured switch current in mA (``SC``)."""
        return self._read_current("SC")

    def current_flow(self) -> float:
        """Measured current flow in mA, continuous mode only (``CA``)."""
        return self._read_current("CA")

    def continuous_limit(self) -> float:
        """Continuous current limit in mA (``L``)."""
        return self._read_current("L")

    def pulse_limit(self) -> float:
        """Pulse/switch current limit in mA (``LP``)."""
        return self._read_current("LP")

    def voltage_limit(self) -> int:
        """Voltage limit in mV (``V``)."""
        return int(self._read_value("V"))

    def pulse_width(self) -> int:
        """Pulse width in microseconds (``D``)."""
        return int(self._read_value("D"))

    def pulse_delay(self) -> int:
        """Pulse delay in microseconds (``PDU``)."""
        return int(self._read_value("PDU"))

    def cooling_time(self) -> int:
        """Pulse cooling time (``PCD``)."""
        return int(self._read_value("PCD"))

    def led_voltage(self) -> int:
        """LED voltage in mV (``UL``)."""
        return int(self._read_value("UL"))

    def led_voltage_in(self) -> int:
        """LED input voltage in mV (``ULI``)."""
        return int(self._read_value("ULI"))

    def led_voltage_out(self) -> int:
        """LED output voltage in mV (``ULO``)."""
        return int(self._read_value("ULO"))

    def last_pulse_voltage(self) -> int:
        """Last-pulse voltage in mV (``LPV``)."""
        return int(self._read_value("LPV"))

    def last_pulse_current(self) -> float:
        """Last-pulse current in mA (``LPC``)."""
        return self._read_current("LPC")

    def pulse_input_polarity(self) -> TriggerEdge:
        """Pulse-mode trigger input edge readback (``I``)."""
        return TriggerEdge.from_wire(self._read_value("I"))

    def switch_input_polarity(self) -> bool:
        """Switch input polarity readback (``ST``): True if active-high."""
        return self._read_value("ST").strip() == "1"

    def output_enabled(self) -> bool:
        """Trigger output enabled readback (``O``)."""
        return self._read_value("O").strip() == "1"

    def output_polarity(self) -> TriggerEdge:
        """Trigger output polarity readback (``OTE``)."""
        return TriggerEdge.from_wire(self._read_value("OTE"))

    def output_source(self) -> OutputTriggerSource:
        """Trigger output source readback (``OTS``)."""
        return OutputTriggerSource.from_wire(self._read_value("OTS"))

    def output_type(self) -> OutputTriggerType:
        """Trigger output type readback (``OTT``)."""
        return OutputTriggerType.from_wire(self._read_value("OTT"))

    def output_delay(self) -> int:
        """Trigger output delay in microseconds readback (``OTD``)."""
        return int(self._read_value("OTD"))

    def output_length(self) -> int:
        """Trigger output length in microseconds readback (``OTL``)."""
        return int(self._read_value("OTL"))

    def is_persisted(self) -> bool:
        """True if this channel's Permanent memory matches Temporary (``EQ``)."""
        return self._read_value("EQ").strip() == "1"

    # --- actions / persistence -----------------------------------------

    def reset(self) -> None:
        """Reset this channel to its defaults (``FR``)."""
        self._set("FR")

    def save(self) -> None:
        """Promote this channel's Temporary memory to Permanent memory (``S``).

        Setters only touch Temporary memory; this is the explicit step that
        makes the channel's config survive a restart (CONTEXT.md).
        """
        self._set("S")
