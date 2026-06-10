"""Tests for :class:`lucon.channel.Channel` — the per-channel command set.

Every behavior is exercised against a live :class:`lucon.testing.FakeLucon`
over real UDP loopback through the genuine :class:`lucon.transport.Transport`
(NOT mocks). Setters are asserted by inspecting the value(s) that landed in the
fake's Temporary memory (``fake._memory[(channel_num, cmd)]``); reads are
scripted with ``fake.set_read(channel_num, cmd, value)``.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import pytest

from lucon.channel import Channel
from lucon.enums import Mode, OutputTriggerSource, OutputTriggerType, TriggerEdge
from lucon.exceptions import LuconCommandError, LuconError
from lucon.lucon import Lucon
from lucon.testing import FakeLucon


@contextmanager
def connected(
    *, online_channels: set[int] | None = None, current_tenths: bool = False
) -> Iterator[tuple[FakeLucon, Lucon]]:
    """Yield a started FakeLucon and an opened Lucon wired to it."""
    with FakeLucon(online_channels=online_channels) as fake:
        host, port = fake.address
        with Lucon(host, port, current_tenths=current_tenths) as lucon:
            yield fake, lucon


def _sent(fake: FakeLucon, channel_num: int, cmd: str) -> tuple[str, ...] | None:
    return fake._memory.get((channel_num, cmd))


# --- channel_num math ------------------------------------------------------


@pytest.mark.parametrize(
    "online,offset,local,expected",
    [
        ({1, 2, 3, 4}, 0, 1, 1),
        ({1, 2, 3, 4}, 0, 4, 4),
        (set(range(1, 9)), 1, 1, 5),
        (set(range(1, 9)), 1, 4, 8),
        (set(range(1, 13)), 2, 3, 11),
    ],
)
def test_channel_num_math(
    online: set[int], offset: int, local: int, expected: int
) -> None:
    with connected(online_channels=online) as (_fake, lucon):
        ch = lucon.controller(offset).channel(local)
        assert ch.channel_num == expected


def test_channel_rejects_bad_local_index() -> None:
    with connected() as (_fake, lucon):
        with pytest.raises(ValueError):
            Channel(lucon.controller(0), 9)


# --- mode setters ----------------------------------------------------------


def test_set_continuous_emits_mc_formatted_current() -> None:
    with connected() as (fake, lucon):
        lucon.channel(1).set_continuous(100.0)
    assert _sent(fake, 1, "MC") == ("100",)


def test_set_continuous_sub45_uses_tenths_format() -> None:
    with connected() as (fake, lucon):
        lucon.channel(1).set_continuous(10.9)
    assert _sent(fake, 1, "MC") == ("10.9",)


def test_set_continuous_over_max_raises_without_wire() -> None:
    with connected() as (fake, lucon):
        with pytest.raises(ValueError):
            lucon.channel(1).set_continuous(3001)
        assert _sent(fake, 1, "MC") is None


def test_set_switch_current_emits_mt() -> None:
    with connected() as (fake, lucon):
        lucon.channel(2).set_switch_current(5000)
    assert _sent(fake, 2, "MT") == ("5000",)


def test_set_switch_current_over_max_raises_without_wire() -> None:
    with connected() as (fake, lucon):
        with pytest.raises(ValueError):
            lucon.channel(2).set_switch_current(20001)
        assert _sent(fake, 2, "MT") is None


def test_set_pulse_emits_mdu_with_microsecond_slots() -> None:
    with connected() as (fake, lucon):
        lucon.channel(3).set_pulse(8000, delay_us=100, duration_us=500)
    # MDU|<current>|<delay_us>|<duration_us>
    assert _sent(fake, 3, "MDU") == ("8000", "100", "500")


def test_set_pulse_current_over_max_raises_without_wire() -> None:
    with connected() as (fake, lucon):
        with pytest.raises(ValueError):
            lucon.channel(3).set_pulse(20001, delay_us=100, duration_us=500)
        assert _sent(fake, 3, "MDU") is None


def test_set_pulse_duration_out_of_range_raises_without_wire() -> None:
    with connected() as (fake, lucon):
        with pytest.raises(ValueError):
            lucon.channel(3).set_pulse(8000, delay_us=100, duration_us=4)
        assert _sent(fake, 3, "MDU") is None


def test_set_pulse_delay_out_of_range_raises_without_wire() -> None:
    with connected() as (fake, lucon):
        with pytest.raises(ValueError):
            lucon.channel(3).set_pulse(8000, delay_us=2, duration_us=500)
        assert _sent(fake, 3, "MDU") is None


def test_set_none_emits_mn() -> None:
    with connected() as (fake, lucon):
        lucon.channel(4).set_none()
    assert _sent(fake, 4, "MN") == ()


# --- limits ----------------------------------------------------------------


def test_set_continuous_limit_emits_l() -> None:
    with connected() as (fake, lucon):
        lucon.channel(1).set_continuous_limit(2500)
    assert _sent(fake, 1, "L") == ("2500",)


def test_set_continuous_limit_over_max_raises_without_wire() -> None:
    with connected() as (fake, lucon):
        with pytest.raises(ValueError):
            lucon.channel(1).set_continuous_limit(3001)
        assert _sent(fake, 1, "L") is None


def test_set_pulse_limit_emits_lp() -> None:
    with connected() as (fake, lucon):
        lucon.channel(1).set_pulse_limit(15000)
    assert _sent(fake, 1, "LP") == ("15000",)


def test_set_pulse_limit_over_max_raises_without_wire() -> None:
    with connected() as (fake, lucon):
        with pytest.raises(ValueError):
            lucon.channel(1).set_pulse_limit(20001)
        assert _sent(fake, 1, "LP") is None


def test_set_voltage_limit_emits_v() -> None:
    with connected() as (fake, lucon):
        lucon.channel(1).set_voltage_limit(24000)
    assert _sent(fake, 1, "V") == ("24000",)


@pytest.mark.parametrize("bad_mv", [999, 60001])
def test_set_voltage_limit_out_of_range_raises_without_wire(bad_mv: int) -> None:
    with connected() as (fake, lucon):
        with pytest.raises(ValueError):
            lucon.channel(1).set_voltage_limit(bad_mv)
        assert _sent(fake, 1, "V") is None


# --- trigger / switch config setters ---------------------------------------


def test_set_switch_input_polarity_high_emits_st_one() -> None:
    with connected() as (fake, lucon):
        lucon.channel(1).set_switch_input_polarity(True)
    assert _sent(fake, 1, "ST") == ("1",)


def test_set_switch_input_polarity_low_emits_st_zero() -> None:
    with connected() as (fake, lucon):
        lucon.channel(1).set_switch_input_polarity(False)
    assert _sent(fake, 1, "ST") == ("0",)


def test_set_switch_current_value_emits_sc() -> None:
    with connected() as (fake, lucon):
        lucon.channel(1).set_switch_current_value(3000)
    assert _sent(fake, 1, "SC") == ("3000",)


def test_set_pulse_input_polarity_emits_i_with_enum_code() -> None:
    with connected() as (fake, lucon):
        lucon.channel(1).set_pulse_input_polarity(TriggerEdge.FALLING)
    assert _sent(fake, 1, "I") == (TriggerEdge.FALLING.code,)


def test_set_output_enabled_emits_o_one() -> None:
    with connected() as (fake, lucon):
        lucon.channel(1).set_output_enabled(True)
    assert _sent(fake, 1, "O") == ("1",)


def test_set_output_enabled_false_emits_o_zero() -> None:
    with connected() as (fake, lucon):
        lucon.channel(1).set_output_enabled(False)
    assert _sent(fake, 1, "O") == ("0",)


def test_set_output_polarity_emits_ote() -> None:
    with connected() as (fake, lucon):
        lucon.channel(1).set_output_polarity(TriggerEdge.RISING)
    assert _sent(fake, 1, "OTE") == (TriggerEdge.RISING.code,)


def test_set_output_polarity_both_raises_without_wire() -> None:
    with connected() as (fake, lucon):
        with pytest.raises(ValueError):
            lucon.channel(1).set_output_polarity(TriggerEdge.BOTH)
        assert _sent(fake, 1, "OTE") is None


def test_set_output_source_emits_ots() -> None:
    with connected() as (fake, lucon):
        lucon.channel(1).set_output_source(OutputTriggerSource.LIGHTING)
    assert _sent(fake, 1, "OTS") == (OutputTriggerSource.LIGHTING.code,)


def test_set_output_type_emits_ott() -> None:
    with connected() as (fake, lucon):
        lucon.channel(1).set_output_type(OutputTriggerType.WHILE_LIT)
    assert _sent(fake, 1, "OTT") == (OutputTriggerType.WHILE_LIT.code,)


def test_set_output_delay_emits_otd() -> None:
    with connected() as (fake, lucon):
        lucon.channel(1).set_output_delay(2500)
    assert _sent(fake, 1, "OTD") == ("2500",)


@pytest.mark.parametrize("bad_us", [-1, 1_000_001])
def test_set_output_delay_out_of_range_raises_without_wire(bad_us: int) -> None:
    with connected() as (fake, lucon):
        with pytest.raises(ValueError):
            lucon.channel(1).set_output_delay(bad_us)
        assert _sent(fake, 1, "OTD") is None


def test_set_output_length_emits_otl() -> None:
    with connected() as (fake, lucon):
        lucon.channel(1).set_output_length(1000)
    assert _sent(fake, 1, "OTL") == ("1000",)


@pytest.mark.parametrize("bad_us", [19, 1_000_001])
def test_set_output_length_out_of_range_raises_without_wire(bad_us: int) -> None:
    with connected() as (fake, lucon):
        with pytest.raises(ValueError):
            lucon.channel(1).set_output_length(bad_us)
        assert _sent(fake, 1, "OTL") is None


# --- reads -----------------------------------------------------------------


def test_temperature_parses_t() -> None:
    with connected() as (fake, lucon):
        fake.set_read(1, "T", "42")
        assert lucon.channel(1).temperature() == 42.0


def test_mode_parses_cm_continuous() -> None:
    with connected() as (fake, lucon):
        fake.set_read(1, "CM", "2")
        assert lucon.channel(1).mode() is Mode.CONTINUOUS


def test_mode_parses_cm_pulse() -> None:
    with connected() as (fake, lucon):
        fake.set_read(1, "CM", "4")
        assert lucon.channel(1).mode() is Mode.PULSE


def test_pulse_current_parses_pc() -> None:
    with connected() as (fake, lucon):
        fake.set_read(1, "PC", "8000")
        assert lucon.channel(1).pulse_current() == 8000.0


def test_switch_current_read_parses_sc() -> None:
    with connected() as (fake, lucon):
        fake.set_read(1, "SC", "3000")
        assert lucon.channel(1).switch_current() == 3000.0


def test_current_flow_parses_ca() -> None:
    with connected() as (fake, lucon):
        fake.set_read(1, "CA", "1234")
        assert lucon.channel(1).current_flow() == 1234.0


def test_current_flow_tenths_mode_divides_by_ten() -> None:
    with connected(current_tenths=True) as (fake, lucon):
        fake.set_read(1, "CA", "354")
        assert lucon.channel(1).current_flow() == 35.4


def test_current_flow_decimal_point_wins_over_tenths() -> None:
    with connected(current_tenths=True) as (fake, lucon):
        fake.set_read(1, "CA", "35.4")
        assert lucon.channel(1).current_flow() == 35.4


def test_continuous_limit_read_parses_l() -> None:
    with connected() as (fake, lucon):
        fake.set_read(1, "L", "2500")
        assert lucon.channel(1).continuous_limit() == 2500.0


def test_pulse_limit_read_parses_lp() -> None:
    with connected() as (fake, lucon):
        fake.set_read(1, "LP", "15000")
        assert lucon.channel(1).pulse_limit() == 15000.0


def test_voltage_limit_read_parses_v() -> None:
    with connected() as (fake, lucon):
        fake.set_read(1, "V", "24000")
        assert lucon.channel(1).voltage_limit() == 24000


def test_pulse_width_read_parses_d() -> None:
    with connected() as (fake, lucon):
        fake.set_read(1, "D", "500")
        assert lucon.channel(1).pulse_width() == 500


def test_pulse_delay_read_parses_pdu() -> None:
    with connected() as (fake, lucon):
        fake.set_read(1, "PDU", "100")
        assert lucon.channel(1).pulse_delay() == 100


def test_cooling_time_read_parses_pcd() -> None:
    with connected() as (fake, lucon):
        fake.set_read(1, "PCD", "5000")
        assert lucon.channel(1).cooling_time() == 5000


def test_led_voltages_read() -> None:
    with connected() as (fake, lucon):
        fake.set_read(1, "UL", "12000")
        fake.set_read(1, "ULI", "12500")
        fake.set_read(1, "ULO", "11500")
        ch = lucon.channel(1)
        assert ch.led_voltage() == 12000
        assert ch.led_voltage_in() == 12500
        assert ch.led_voltage_out() == 11500


def test_last_pulse_voltage_and_current_read() -> None:
    with connected() as (fake, lucon):
        fake.set_read(1, "LPV", "13000")
        fake.set_read(1, "LPC", "7950")
        ch = lucon.channel(1)
        assert ch.last_pulse_voltage() == 13000
        assert ch.last_pulse_current() == 7950.0


# --- enum readbacks --------------------------------------------------------


def test_pulse_input_polarity_read_parses_enum() -> None:
    with connected() as (fake, lucon):
        fake.set_read(1, "I", "1")
        assert lucon.channel(1).pulse_input_polarity() is TriggerEdge.FALLING


def test_pulse_input_polarity_read_accepts_letter_form() -> None:
    with connected() as (fake, lucon):
        fake.set_read(1, "I", "B")
        assert lucon.channel(1).pulse_input_polarity() is TriggerEdge.BOTH


def test_switch_input_polarity_read() -> None:
    with connected() as (fake, lucon):
        fake.set_read(1, "ST", "1")
        assert lucon.channel(1).switch_input_polarity() is True


def test_output_enabled_read() -> None:
    with connected() as (fake, lucon):
        fake.set_read(1, "O", "1")
        assert lucon.channel(1).output_enabled() is True


def test_output_polarity_read_parses_enum() -> None:
    with connected() as (fake, lucon):
        fake.set_read(1, "OTE", "0")
        assert lucon.channel(1).output_polarity() is TriggerEdge.RISING


@pytest.mark.parametrize("wire", ["1", "2"])  # fw 0.5.0 reads/sets lighting as 1; stray 2 tolerated
def test_output_source_read_parses_enum(wire: str) -> None:
    with connected() as (fake, lucon):
        fake.set_read(1, "OTS", wire)
        assert lucon.channel(1).output_source() is OutputTriggerSource.LIGHTING


def test_output_type_read_parses_enum() -> None:
    with connected() as (fake, lucon):
        fake.set_read(1, "OTT", "1")
        assert lucon.channel(1).output_type() is OutputTriggerType.WHILE_LIT


def test_output_delay_read() -> None:
    with connected() as (fake, lucon):
        fake.set_read(1, "OTD", "2500")
        assert lucon.channel(1).output_delay() == 2500


def test_output_length_read() -> None:
    with connected() as (fake, lucon):
        fake.set_read(1, "OTL", "1000")
        assert lucon.channel(1).output_length() == 1000


def test_is_persisted_true_when_eq_one() -> None:
    with connected() as (fake, lucon):
        fake.set_read(1, "EQ", "1")
        assert lucon.channel(1).is_persisted() is True


def test_is_persisted_false_when_eq_zero() -> None:
    with connected() as (fake, lucon):
        fake.set_read(1, "EQ", "0")
        assert lucon.channel(1).is_persisted() is False


# --- actions / persistence -------------------------------------------------


def test_save_sends_s_on_channel() -> None:
    with connected() as (fake, lucon):
        lucon.channel(1).save()
    assert _sent(fake, 1, "S") == ()


def test_reset_sends_fr_on_channel() -> None:
    with connected() as (fake, lucon):
        lucon.channel(1).reset()
    assert _sent(fake, 1, "FR") == ()


def test_setter_targets_correct_wire_address_for_slave_channel() -> None:
    # A slave's local channel 1 (offset 1) writes to global wire address 05.
    with connected(online_channels=set(range(1, 9))) as (fake, lucon):
        lucon.controller(1).channel(1).set_continuous(100.0)
    assert _sent(fake, 5, "MC") == ("100",)
    assert _sent(fake, 1, "MC") is None


# --- round-trip through Temporary memory -----------------------------------


def test_set_then_read_continuous_round_trips_via_fake_memory() -> None:
    # FakeLucon reflects S<cc>MC into R<cc>CA (continuous current flow).
    with connected() as (_fake, lucon):
        ch = lucon.channel(1)
        ch.set_continuous(250.0)
        assert ch.current_flow() == 250.0


# --- error paths -----------------------------------------------------------


def test_device_rejection_on_setter_raises_command_error() -> None:
    with connected() as (fake, lucon):
        fake.fail_next("value out of range", channel=1, cmd="MC")
        with pytest.raises(LuconCommandError):
            lucon.channel(1).set_continuous(100.0)


def test_empty_read_reply_raises_lucon_error() -> None:
    with connected() as (fake, lucon):
        fake.set_read(1, "T", "")  # echo-only reply, no value token
        with pytest.raises(LuconError):
            lucon.channel(1).temperature()
