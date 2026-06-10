"""One physical LUCON unit (:class:`Controller`) in a chain.

A :class:`Controller` is one LUCON 4C-20A-V unit at a given **Controller
offset** (master = 0, first slave = 1, …). It owns exactly four
:class:`~lucon.channel.Channel` outputs, addressed by *local* index 1–4, whose
global wire addresses are ``offset * 4 + local_index``.

**Device identity is master-only.** General ``00`` commands physically reach
only the master (CONTEXT.md / ADR-0002), so a slave Controller cannot report
its own serial / firmware / supply voltage over UDP. Those reads live on
:class:`~lucon.lucon.Lucon` (the master). A Controller therefore exposes only
channel operations.

Persistence is per channel via :meth:`Channel.save`; the chain-wide
``S00S`` save (with its 0/1/2 scope) fans out at the :class:`~lucon.lucon.Lucon`
level, so there is no separate controller-level save command.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lucon.channel import Channel

if TYPE_CHECKING:
    from lucon.lucon import Lucon


class Controller:
    """One LUCON unit at a fixed offset, owning four :class:`Channel` outputs.

    Construct via :class:`~lucon.lucon.Lucon`, not directly.
    """

    def __init__(self, lucon: Lucon, offset: int) -> None:
        if offset < 0:
            raise ValueError(f"offset must be >= 0, got {offset}")
        self._lucon = lucon
        self._offset = offset
        self._channels: list[Channel] = [Channel(self, i) for i in range(1, 5)]

    @property
    def offset(self) -> int:
        """This Controller's position in the chain (master = 0)."""
        return self._offset

    @property
    def is_master(self) -> bool:
        """True for the master Controller (offset 0), which answers ``00`` commands."""
        return self._offset == 0

    @property
    def channels(self) -> list[Channel]:
        """This Controller's four :class:`Channel`s, ordered by channel_num."""
        return list(self._channels)

    def channel(self, local_index: int) -> Channel:
        """Return the :class:`Channel` at ``local_index`` (1–4)."""
        if not 1 <= local_index <= 4:
            raise ValueError(f"local_index must be 1-4, got {local_index}")
        return self._channels[local_index - 1]

    def __repr__(self) -> str:
        return f"Controller(offset={self._offset}, is_master={self.is_master})"
