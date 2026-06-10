"""The runtime-toggleable ``Lucon.current_tenths`` property (backs the API toggle)."""

from __future__ import annotations

from lucon import Lucon
from lucon.testing import FakeLucon


def test_current_tenths_defaults_false_and_toggles() -> None:
    fake = FakeLucon()
    host, port = fake.start()
    try:
        with Lucon(host, port) as lucon:
            assert lucon.current_tenths is False
            lucon.current_tenths = True
            assert lucon.current_tenths is True
            lucon.current_tenths = False
            assert lucon.current_tenths is False
    finally:
        fake.stop()


def test_current_tenths_initialized_from_constructor() -> None:
    fake = FakeLucon()
    host, port = fake.start()
    try:
        with Lucon(host, port, current_tenths=True) as lucon:
            assert lucon.current_tenths is True
    finally:
        fake.stop()
