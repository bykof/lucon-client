"""Tests for the LUCON domain enums.

Each enum maps to/from the exact device wire tokens used in SET/READ commands:

    Mode                <- CM value (0/2/3/4/7)
    TriggerEdge         <- pulse input polarity ``I`` (0/1/2, also R/F/B on SET)
    OutputTriggerSource <- ``OTS`` value (0/1; a stray manual ``2`` tolerated on READ)
    OutputTriggerType   <- ``OTT`` value (0/1)

Every enum exposes a ``.code`` (the token a SET must send) and a
``from_wire`` classmethod (parse a device READ value back to the member).
Behaviour is verified through that public interface, not internals.
"""

import pytest

from lucon.enums import Mode, OutputTriggerSource, OutputTriggerType, TriggerEdge


def test_mode_code_is_the_cm_wire_token() -> None:
    # CONTEXT.md / CM: NONE=0, CONTINUOUS=2, SWITCH=3, PULSE=4, ERROR=7.
    assert Mode.CONTINUOUS.code == "2"


def test_mode_from_wire_parses_cm_value() -> None:
    # A R..CM read reply returns the numeric token; map it back to the member.
    assert Mode.from_wire("7") == Mode.ERROR


@pytest.mark.parametrize("token", ["", "1", "5", "9", "xx"])
def test_mode_from_wire_rejects_unknown_token(token: str) -> None:
    # CM only defines 0/2/3/4/7; anything else is not a known mode.
    with pytest.raises(ValueError):
        Mode.from_wire(token)


@pytest.mark.parametrize("mode", list(Mode))
def test_mode_round_trips_through_the_wire(mode: Mode) -> None:
    # code -> from_wire is the identity for every defined mode.
    assert Mode.from_wire(mode.code) is mode


@pytest.mark.parametrize(
    ("edge", "code"),
    [(TriggerEdge.RISING, "0"), (TriggerEdge.FALLING, "1"), (TriggerEdge.BOTH, "2")],
)
def test_trigger_edge_code_is_the_numeric_i_token(edge: TriggerEdge, code: str) -> None:
    # Pulse input polarity I: RISING=0, FALLING=1, BOTH=2. SET sends the digit.
    assert edge.code == code


@pytest.mark.parametrize(
    ("token", "edge"),
    [("0", TriggerEdge.RISING), ("1", TriggerEdge.FALLING), ("2", TriggerEdge.BOTH)],
)
def test_trigger_edge_from_wire_parses_numeric(token: str, edge: TriggerEdge) -> None:
    # A R..I read reply returns the numeric token.
    assert TriggerEdge.from_wire(token) is edge


@pytest.mark.parametrize(
    ("token", "edge"),
    [
        ("R", TriggerEdge.RISING),
        ("F", TriggerEdge.FALLING),
        ("B", TriggerEdge.BOTH),
        ("r", TriggerEdge.RISING),
        ("f", TriggerEdge.FALLING),
        ("b", TriggerEdge.BOTH),
        (" R ", TriggerEdge.RISING),
    ],
)
def test_trigger_edge_from_wire_parses_letter_form(
    token: str, edge: TriggerEdge
) -> None:
    # The device tolerates the letter form R/F/B (case-insensitive) on I; a
    # read reply may report it, so from_wire must accept it per its docstring.
    assert TriggerEdge.from_wire(token) is edge


@pytest.mark.parametrize("token", ["", "3", "9", "xx", "RF"])
def test_trigger_edge_from_wire_rejects_unknown_token(token: str) -> None:
    # Only 0/1/2 and R/F/B (any case) are valid; everything else is rejected.
    with pytest.raises(ValueError):
        TriggerEdge.from_wire(token)


# --- OutputTriggerSource (OTS) --------------------------------------------


@pytest.mark.parametrize(
    ("source", "code"),
    [(OutputTriggerSource.INPUT, "0"), (OutputTriggerSource.LIGHTING, "1")],
)
def test_output_trigger_source_code_is_the_ots_token(
    source: OutputTriggerSource, code: str
) -> None:
    # OTS (confirmed fw 0.5.0): 0 = input, 1 = lighting. SET sends the digit.
    assert source.code == code


@pytest.mark.parametrize(
    ("token", "source"),
    [
        ("0", OutputTriggerSource.INPUT),
        (
            "1",
            OutputTriggerSource.LIGHTING,
        ),  # confirmed fw 0.5.0 token (both directions)
        (
            "2",
            OutputTriggerSource.LIGHTING,
        ),  # tolerated: manual SET-table value, rejected by hw
    ],
)
def test_output_trigger_source_from_wire(
    token: str, source: OutputTriggerSource
) -> None:
    assert OutputTriggerSource.from_wire(token) is source


@pytest.mark.parametrize("token", ["", "3", "9", "xx"])
def test_output_trigger_source_from_wire_rejects_unknown_token(token: str) -> None:
    with pytest.raises(ValueError):
        OutputTriggerSource.from_wire(token)


# --- OutputTriggerType (OTT) ----------------------------------------------


@pytest.mark.parametrize(
    ("kind", "code"),
    [(OutputTriggerType.TIME_LIMITED, "0"), (OutputTriggerType.WHILE_LIT, "1")],
)
def test_output_trigger_type_code_is_the_ott_token(
    kind: OutputTriggerType, code: str
) -> None:
    # OTT: 0 = time-limited, 1 = while-lit. SET sends the digit.
    assert kind.code == code


@pytest.mark.parametrize(
    ("token", "kind"),
    [("0", OutputTriggerType.TIME_LIMITED), ("1", OutputTriggerType.WHILE_LIT)],
)
def test_output_trigger_type_from_wire(token: str, kind: OutputTriggerType) -> None:
    assert OutputTriggerType.from_wire(token) is kind


@pytest.mark.parametrize("token", ["", "2", "9", "xx"])
def test_output_trigger_type_from_wire_rejects_unknown_token(token: str) -> None:
    with pytest.raises(ValueError):
        OutputTriggerType.from_wire(token)
