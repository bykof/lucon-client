"""Tests for :class:`lucon.controller.Controller`.

Exercised against a live :class:`lucon.testing.FakeLucon` over real UDP
loopback through the genuine :class:`lucon.transport.Transport` (NOT mocks), so
the offset/topology behavior is observed through the public API.
"""

from __future__ import annotations

import pytest

from lucon.controller import Controller
from lucon.lucon import Lucon
from lucon.testing import FakeLucon

# --- offset / master vs slave ---------------------------------------------


def test_master_controller_has_offset_zero_and_is_master() -> None:
    with FakeLucon() as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            master = lucon.controller(0)
            assert master.offset == 0
            assert master.is_master is True


def test_slave_controller_is_not_master() -> None:
    with FakeLucon(online_channels=set(range(1, 9))) as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            slave = lucon.controller(1)
            assert slave.offset == 1
            assert slave.is_master is False


# --- channels list ---------------------------------------------------------


def test_channels_list_has_four_channels_ordered_by_channel_num() -> None:
    with FakeLucon(online_channels=set(range(1, 9))) as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            slave = lucon.controller(1)
            assert [c.channel_num for c in slave.channels] == [5, 6, 7, 8]
            assert [c.local_index for c in slave.channels] == [1, 2, 3, 4]


def test_channels_property_returns_a_copy() -> None:
    with FakeLucon() as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            master = lucon.controller(0)
            channels = master.channels
            channels.clear()
            # Mutating the returned list must not affect the Controller.
            assert len(master.channels) == 4


# --- channel(local) bounds -------------------------------------------------


def test_channel_local_index_maps_to_channel_num() -> None:
    with FakeLucon(online_channels=set(range(1, 9))) as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            slave = lucon.controller(1)
            assert slave.channel(1).channel_num == 5
            assert slave.channel(4).channel_num == 8


def test_channel_local_index_back_reference_to_controller() -> None:
    with FakeLucon(online_channels=set(range(1, 9))) as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            slave = lucon.controller(1)
            assert slave.channel(2).controller is slave


@pytest.mark.parametrize("bad", [0, 5, -1])
def test_channel_local_index_out_of_range_raises(bad: int) -> None:
    with FakeLucon() as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            master = lucon.controller(0)
            with pytest.raises(ValueError):
                master.channel(bad)


# --- construction guard ----------------------------------------------------


def test_controller_rejects_negative_offset() -> None:
    with FakeLucon() as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            with pytest.raises(ValueError):
                Controller(lucon, -1)
