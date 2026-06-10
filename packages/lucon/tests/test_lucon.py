"""Tests for :class:`lucon.lucon.Lucon` — the Chain / top-level connection.

These drive the domain tree against a live :class:`lucon.testing.FakeLucon`
over genuine UDP loopback through the real :class:`lucon.transport.Transport`
(NOT mocks), so every behavior is exercised on the wire. Per the project
rules sockets bind to 127.0.0.1:0 (FakeLucon does this internally).

``Lucon`` owns the Transport and every general (channel 00) command, builds
the Controller/Channel tree from ``R00RT`` on open, and exposes the global
``channel(1..96)`` shortcut plus ``controller(offset)``.
"""

from __future__ import annotations

import threading

import pytest

from lucon.codec import Response, ResponseKind
from lucon.exceptions import LuconCommandError, LuconError
from lucon.lucon import Lucon
from lucon.testing import FakeLucon


# --- tree building from R00RT ---------------------------------------------


def test_open_builds_single_controller_for_lone_unit() -> None:
    # Default FakeLucon reports channels 1-4 online -> one Controller (offset 0).
    with FakeLucon() as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            controller = lucon.controller(0)
            assert controller.offset == 0
            assert controller.is_master is True
            assert [c.channel_num for c in controller.channels] == [1, 2, 3, 4]


def test_open_builds_two_controllers_for_eight_online_channels() -> None:
    with FakeLucon(online_channels={1, 2, 3, 4, 5, 6, 7, 8}) as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            assert lucon.offsets == [0, 1]
            master = lucon.controller(0)
            slave = lucon.controller(1)
            assert master.is_master is True
            assert slave.is_master is False
            assert [c.channel_num for c in slave.channels] == [5, 6, 7, 8]


def test_global_channel_shortcut_maps_to_offset_and_local_index() -> None:
    with FakeLucon(online_channels={1, 2, 3, 4, 5, 6, 7, 8}) as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            ch = lucon.channel(5)
            assert ch.channel_num == 5
            assert ch.controller.offset == 1
            assert ch.local_index == 1


def test_controller_for_unknown_offset_raises() -> None:
    with FakeLucon() as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            with pytest.raises(LuconError):
                lucon.controller(5)


def test_global_channel_for_unknown_number_raises() -> None:
    with FakeLucon() as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            with pytest.raises(LuconError):
                lucon.channel(50)


def test_controllers_property_lists_all_controllers() -> None:
    with FakeLucon(online_channels=set(range(1, 9))) as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            offsets = [c.offset for c in lucon.controllers]
            assert offsets == [0, 1]


# --- lifecycle ------------------------------------------------------------


def test_is_open_reflects_lifecycle() -> None:
    with FakeLucon() as fake:
        host, port = fake.address
        lucon = Lucon(host, port)
        assert lucon.is_open is False
        lucon.open()
        try:
            assert lucon.is_open is True
        finally:
            lucon.close()
        assert lucon.is_open is False


def test_close_is_idempotent() -> None:
    with FakeLucon() as fake:
        host, port = fake.address
        lucon = Lucon(host, port)
        lucon.open()
        lucon.close()
        lucon.close()
        assert lucon.is_open is False


# --- general (00) identity reads ------------------------------------------


def test_firmware_reads_r00f() -> None:
    with FakeLucon(firmware="LUCON 4C-20A-V v9.9") as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            assert lucon.firmware() == "LUCON 4C-20A-V v9.9"


def test_serial_reads_r00sn() -> None:
    with FakeLucon(serial="SN-12345") as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            assert lucon.serial() == "SN-12345"


def test_mac_reads_r00mac() -> None:
    with FakeLucon(mac="AA:BB:CC:DD:EE:FF") as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            assert lucon.mac() == "AA:BB:CC:DD:EE:FF"


def test_ip_and_subnet_getters() -> None:
    with FakeLucon() as fake:
        fake.set_read(0, "IP", "192.168.0.10")
        fake.set_read(0, "SM", "255.255.255.0")
        host, port = fake.address
        with Lucon(host, port) as lucon:
            assert lucon.ip() == "192.168.0.10"
            assert lucon.subnet() == "255.255.255.0"


def test_udp_port_read() -> None:
    with FakeLucon() as fake:
        fake.set_read(0, "UDP", "50000")
        host, port = fake.address
        with Lucon(host, port) as lucon:
            assert lucon.udp_port() == 50000


def test_bootloader_read() -> None:
    with FakeLucon() as fake:
        fake.set_read(0, "BLV", "2.3")
        host, port = fake.address
        with Lucon(host, port) as lucon:
            assert lucon.bootloader() == "2.3"


def test_pcb_revision_reads() -> None:
    with FakeLucon() as fake:
        fake.set_read(0, "RCP", "1.4")
        fake.set_read(0, "RPP", "1.5")
        host, port = fake.address
        with Lucon(host, port) as lucon:
            assert lucon.pcb_revision_control() == "1.4"
            assert lucon.pcb_revision_power() == "1.5"


def test_supply_voltage_mv_read() -> None:
    with FakeLucon() as fake:
        fake.set_read(0, "USU", "23950")
        host, port = fake.address
        with Lucon(host, port) as lucon:
            assert lucon.supply_voltage_mv() == 23950


def test_controller_offset_getter() -> None:
    with FakeLucon() as fake:
        fake.set_read(0, "CO", "3")
        host, port = fake.address
        with Lucon(host, port) as lucon:
            assert lucon.controller_offset() == 3


def test_error_buffer_read() -> None:
    with FakeLucon() as fake:
        fake.set_read(0, "M", "no errors")
        host, port = fake.address
        with Lucon(host, port) as lucon:
            assert lucon.error_buffer() == "no errors"


def test_is_persisted_true_when_eq_is_one() -> None:
    with FakeLucon() as fake:
        fake.set_read(0, "EQ", "1")
        host, port = fake.address
        with Lucon(host, port) as lucon:
            assert lucon.is_persisted() is True


def test_is_persisted_false_when_eq_is_zero() -> None:
    with FakeLucon() as fake:
        fake.set_read(0, "EQ", "0")
        host, port = fake.address
        with Lucon(host, port) as lucon:
            assert lucon.is_persisted() is False


# --- general (00) setters / actions ---------------------------------------


def _sent(fake: FakeLucon, channel: int, cmd: str) -> tuple[str, ...] | None:
    return fake._memory.get((channel, cmd))


def test_set_controller_offset_sends_s00co() -> None:
    with FakeLucon() as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            lucon.set_controller_offset(2)
        assert _sent(fake, 0, "CO") == ("2",)


def test_set_ip_sends_s00ip() -> None:
    with FakeLucon() as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            lucon.set_ip("10.0.0.5")
        assert _sent(fake, 0, "IP") == ("10.0.0.5",)


def test_set_subnet_sends_s00sm() -> None:
    with FakeLucon() as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            lucon.set_subnet("255.255.0.0")
        assert _sent(fake, 0, "SM") == ("255.255.0.0",)


def test_save_general_sends_s00s_scope_zero() -> None:
    # Manual: scope 0 = general parameters of this unit only.
    with FakeLucon() as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            lucon.save_general()
        assert _sent(fake, 0, "S") == ("0",)


def test_save_all_sends_s00s_scope_one() -> None:
    # Manual: scope 1 = general AND channel-specific params of this unit.
    with FakeLucon() as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            lucon.save_all()
        assert _sent(fake, 0, "S") == ("1",)


def test_save_chain_channels_uses_scope_two() -> None:
    # Manual: scope 2 = channel params of this AND all connected units.
    with FakeLucon() as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            lucon.save(2)
        assert _sent(fake, 0, "S") == ("2",)


def test_save_and_restart_sends_s00sr() -> None:
    with FakeLucon() as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            lucon.save_and_restart(1)
        assert _sent(fake, 0, "SR") == ("1",)


def test_save_with_explicit_scope() -> None:
    with FakeLucon() as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            lucon.save(0)
        assert _sent(fake, 0, "S") == ("0",)


def test_save_rejects_bad_scope_without_wire() -> None:
    with FakeLucon() as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            with pytest.raises(ValueError):
                lucon.save(3)
        assert _sent(fake, 0, "S") is None


def test_factory_reset_sends_s00fr() -> None:
    with FakeLucon() as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            lucon.factory_reset(1)
        assert _sent(fake, 0, "FR") == ("1",)


def test_factory_reset_rejects_bad_scope() -> None:
    with FakeLucon() as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            with pytest.raises(ValueError):
                lucon.factory_reset(9)
        assert _sent(fake, 0, "FR") is None


def test_set_ip_checked_sends_s00sip() -> None:
    with FakeLucon() as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            lucon.set_ip_checked("10.0.0.7", "SN-12345")
        assert _sent(fake, 0, "SIP") == ("10.0.0.7", "SN-12345")


def test_restart_sends_s00r() -> None:
    with FakeLucon() as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            lucon.restart()
        assert _sent(fake, 0, "R") == ()


# --- escape hatch ---------------------------------------------------------


def test_raw_send_returns_set_ack() -> None:
    from lucon import codec

    with FakeLucon() as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            resp = lucon.send(codec.encode_set(1, "MC", "100"))
    assert resp.kind is ResponseKind.SET_ACK
    assert resp.echo == "S01MC|100"


def test_raw_query_returns_read_reply() -> None:
    from lucon import codec

    with FakeLucon() as fake:
        fake.set_read(1, "T", "27")
        host, port = fake.address
        with Lucon(host, port) as lucon:
            resp = lucon.query(codec.encode_read(1, "T"))
    assert resp.kind is ResponseKind.READ_REPLY
    assert resp.values == ("27",)


# --- callbacks / events ---------------------------------------------------


def test_on_error_callback_fires_for_unsolicited_overtemp() -> None:
    received: list[Response] = []
    fired = threading.Event()

    def on_error(resp: Response) -> None:
        received.append(resp)
        fired.set()

    with FakeLucon() as fake:
        host, port = fake.address
        with Lucon(host, port, on_error=on_error) as lucon:
            fake.inject_error("Overtemperature on Channel 01")
            assert fired.wait(2.0)
            polled = lucon.poll_events(timeout=2.0)

    assert received[0].kind is ResponseKind.ERROR
    assert received[0].message == "Overtemperature on Channel 01"
    assert polled is not None
    assert polled.message == "Overtemperature on Channel 01"


def test_on_event_callback_fires_for_unsolicited_status() -> None:
    received: list[Response] = []
    fired = threading.Event()

    def on_event(resp: Response) -> None:
        received.append(resp)
        fired.set()

    with FakeLucon() as fake:
        host, port = fake.address
        with Lucon(host, port, on_event=on_event) as lucon:
            fake.inject_status("RUNNING...")
            assert fired.wait(2.0)
            polled = lucon.poll_events(timeout=2.0)

    assert received[0].kind is ResponseKind.STATUS
    assert polled is not None
    assert polled.message == "RUNNING..."


def test_poll_events_none_when_empty() -> None:
    with FakeLucon() as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            assert lucon.poll_events() is None


# --- error paths ----------------------------------------------------------


def test_tree_from_gappy_online_list_infers_controllers_by_block() -> None:
    # "Online: 01, 02, 14" -> Controllers at offset 0 and 3 (block of four).
    with FakeLucon(online_channels={1, 2, 14}) as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            assert lucon.offsets == [0, 3]
            assert lucon.channel(14).controller.offset == 3
            with pytest.raises(LuconError):
                lucon.controller(1)


def test_device_error_propagates_as_command_error() -> None:
    with FakeLucon() as fake:
        host, port = fake.address
        with Lucon(host, port) as lucon:
            fake.fail_next("rejected")  # next command after the handshake
            with pytest.raises(LuconCommandError):
                lucon.firmware()


def test_empty_general_reply_raises_lucon_error() -> None:
    with FakeLucon() as fake:
        fake.set_read(0, "M", "")  # echo-only reply, no value token
        host, port = fake.address
        with Lucon(host, port) as lucon:
            with pytest.raises(LuconError):
                lucon.error_buffer()
