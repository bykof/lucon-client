"""Domain enums for the LUCON 4C-20A-V.

Each enum models one set of device wire tokens (the values carried by a SET or
returned by a READ) as named domain members. Members are defined with their wire
token as the enum *value*, so:

* ``.code`` is the token a SET command must send;
* ``from_wire`` parses a device READ value back to the member.

Keeping the mapping in one place stops magic numbers like ``"2"`` for
:class:`Mode` leaking into the channel/command layers above.
"""

from __future__ import annotations

import enum


class Mode(enum.Enum):
    """Operating mode of a Channel, as carried by the ``CM`` command/READ.

    See CONTEXT.md: NONE (idle), CONTINUOUS (software mode), SWITCH, PULSE, and
    the read-only ERROR state a faulted channel reports.
    """

    NONE = "0"
    CONTINUOUS = "2"
    SWITCH = "3"
    PULSE = "4"
    ERROR = "7"

    @property
    def code(self) -> str:
        """The wire token for this mode (the ``CM`` value)."""
        return self.value

    @classmethod
    def from_wire(cls, token: str) -> Mode:
        """Parse a device ``CM`` value token (e.g. ``"2"``) into a member."""
        return cls(token.strip())


class TriggerEdge(enum.Enum):
    """Trigger edge / polarity for the pulse input (``I``) command.

    SET ``I`` accepts either the numeric token (``0``/``1``/``2``) or the letter
    form (``R``/``F``/``B``); we send the numeric :attr:`code` and accept both
    when parsing a device value.

    The trigger *output* polarity (``OTE``) reuses this enum's rising/falling
    subset only: ``OTE`` is ``0``/``R`` or ``1``/``F`` — :attr:`BOTH` is invalid
    for ``OTE``, since an output fires on a single edge.
    """

    RISING = "0"
    FALLING = "1"
    BOTH = "2"

    @property
    def code(self) -> str:
        """The numeric wire token for this edge (the ``I`` value SET sends)."""
        return self.value

    @classmethod
    def from_wire(cls, token: str) -> TriggerEdge:
        """Parse an ``I`` value into a member, numeric or letter form.

        Accepts both the digit (``"0"``/``"1"``/``"2"``) and the letter
        (``"R"``/``"F"``/``"B"``, case-insensitive) the device tolerates on SET.
        Raises :class:`ValueError` for any other token.
        """
        stripped = token.strip()
        letter = {"R": cls.RISING, "F": cls.FALLING, "B": cls.BOTH}.get(
            stripped.upper()
        )
        if letter is not None:
            return letter
        return cls(stripped)


class OutputTriggerSource(enum.Enum):
    """Source of the trigger *output*, as carried by the ``OTS`` command/READ.

    The output can be driven by the channel's trigger input (INPUT) or by the
    lighting state (LIGHTING). **Confirmed on hardware (fw 0.5.0):** ``OTS`` uses
    ``0`` = INPUT and ``1`` = LIGHTING in *both* directions, and ``2`` is
    rejected ("out of range"). The manual's SET table (§7.4.2.3) showing ``2``
    for LIGHTING is wrong for this firmware; the READ table's ``1`` is correct.
    :meth:`from_wire` still tolerates a stray ``2`` as LIGHTING defensively, in
    case other firmware follows the manual.
    """

    INPUT = "0"
    LIGHTING = "1"

    @property
    def code(self) -> str:
        """The wire token for this source (the ``OTS`` value SET sends, ``1`` for LIGHTING)."""
        return self.value

    @classmethod
    def from_wire(cls, token: str) -> OutputTriggerSource:
        """Parse a device ``OTS`` READ value into a member.

        ``0`` -> INPUT, ``1`` -> LIGHTING (the confirmed fw 0.5.0 tokens). A stray
        ``2`` (the manual's SET-table value, rejected by real hardware) is
        tolerated as LIGHTING for robustness against firmware that follows it.
        """
        stripped = token.strip()
        if stripped == "2":  # manual SET-table token; real fw 0.5.0 uses 1
            return cls.LIGHTING
        return cls(stripped)  # "0" -> INPUT, "1" -> LIGHTING, else ValueError


class OutputTriggerType(enum.Enum):
    """Type of the trigger *output*, as carried by the ``OTT`` command/READ.

    The output is either time-limited (``OTT``=0, fires for a fixed length) or
    held for as long as the channel is lit (``OTT``=1, while-lit).
    """

    TIME_LIMITED = "0"
    WHILE_LIT = "1"

    @property
    def code(self) -> str:
        """The wire token for this type (the ``OTT`` value SET sends)."""
        return self.value

    @classmethod
    def from_wire(cls, token: str) -> OutputTriggerType:
        """Parse a device ``OTT`` value token into a member."""
        return cls(token.strip())
