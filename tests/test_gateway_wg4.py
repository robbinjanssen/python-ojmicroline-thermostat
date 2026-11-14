"""Test the WG4 device messages of the gateway.

The frames marked as real were captured from thermostats by
peskyAdmin/schluter-local (MIT). They hold no account or device identifiers.

Setpoints on the wire are in 1/200 °C while the library uses 1/100 °C. Getting
that scale wrong writes half the intended temperature, so it is pinned here.
So is the field each hold keeps its setpoint in: a permanent hold written to
the temporary hold's field leaves the thermostat at its old permanent setpoint.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from freezegun import freeze_time

from ojmicroline_thermostat.gateway.protobuf import (
    VARINT,
    Field,
    Message,
    encode_field,
)
from ojmicroline_thermostat.gateway.wg4 import (
    EPOCH,
    FIELD_CONFIGURATION,
    FIELD_SUPPORT,
    acknowledgement,
    changed_state,
    command,
    decode_state,
    decode_telemetry,
    describe,
    device_time,
    login_acknowledgement,
    login_serial,
    response_field,
)

# A login shaped like the thermostat's: the account's email address, the serial
# number and a number that is not used. The values are made up.
ACCOUNT = (
    encode_field(1, b"user@example.com")
    + encode_field(2, b"1234567")
    + encode_field(3, 10207)
)
LOGIN = encode_field(1, 29) + encode_field(5, 1) + encode_field(6, ACCOUNT)

# The real answer of OJ's gateway to a login with sequence number 29.
CLOUD_LOGIN_ACKNOWLEDGEMENT = bytes.fromhex("081d18012001620608c5e4f49203")

# The state of a real upload while the thermostat held 58 °F permanently: mode
# 3, temporary setpoint 9.4.1 = 2666 (13.33 °C) and permanent setpoint
# 9.5.1 = 2888 (14.44 °C, 58 °F).
PERMANENT_STATE = bytes.fromhex(
    "08c5e4f4920310031a020800220908ea1410809cf692032a0308c8163000"
)

# Real messages: a heartbeat, a setpoint change made at the thermostat during
# a temporary hold at 71 °F, and the device info that followed it.
TELEMETRY = bytes.fromhex("08930328017a0e08f10f1000200028003000388c04")
WALL_STATE = bytes.fromhex(
    "08960328014a1e08cedbf6920310021a020800220908ec2110e0c4f792032a0308c8163000"
)
WALL_INFO = bytes.fromhex(
    "08970328013a1708d0dbf6920330e80738c03e48ec215001581968017064"
)

# The heartbeat (field 15) of two real messages, around the moment the floor
# started to heat.
IDLE = bytes.fromhex("08f10f1000200028003000388c04")
HEATING = bytes.fromhex("08ee0f1001200028003000389904")


def _state(mode: int, *, temporary: int = 3332, permanent: int = 4444) -> bytes:
    """Build a state shaped like the thermostat's, with setpoints in 1/200 °C."""
    return (
        encode_field(1, 1000)
        + encode_field(2, mode)
        + encode_field(4, encode_field(1, temporary))
        + encode_field(5, encode_field(1, permanent))
        + encode_field(6, 0)
    )


def _content(data: bytes, number: int) -> bytes:
    """Return the content of a length-delimited field that has to be there."""
    content = Message.parse(data).data(number)
    assert content is not None
    return content


def _other_fields(state: bytes, *changed: int) -> list[Field]:
    """Return the fields of a state that a command should leave alone."""
    return [
        field for field in Message.parse(state).fields if field.number not in changed
    ]


def test_device_time() -> None:
    """The thermostat counts seconds since the start of 2000, in UTC."""
    assert device_time(datetime(2000, 1, 1, tzinfo=UTC)) == 0
    assert device_time(datetime(2000, 1, 1, 0, 1, 0, 500000, tzinfo=UTC)) == 60
    # 03:00 at UTC+1 is 02:00 UTC.
    elsewhere = datetime(2000, 1, 1, 3, tzinfo=timezone(timedelta(hours=1)))
    assert device_time(elsewhere) == 7200


@freeze_time("2026-10-10 12:00:00")
def test_device_time_defaults_to_now() -> None:
    """Without a moment the current time is used."""
    assert device_time() == device_time(datetime(2026, 10, 10, 12, tzinfo=UTC))


def test_acknowledgement() -> None:
    """An acknowledgement repeats the sequence number with field 3 set to 1."""
    assert acknowledgement(403) == bytes.fromhex("0893031801")


def test_login_acknowledgement_matches_the_cloud() -> None:
    """The answer to a login is byte for byte what OJ's gateway sends."""
    answer = Message.parse(CLOUD_LOGIN_ACKNOWLEDGEMENT)
    server_time = answer.message(12)
    assert server_time is not None
    seconds = server_time.varint(1)
    assert seconds is not None

    moment = EPOCH + timedelta(seconds=seconds)
    assert login_acknowledgement(29, moment) == CLOUD_LOGIN_ACKNOWLEDGEMENT


@freeze_time("2026-10-10 12:00:00")
def test_login_acknowledgement_defaults_to_now() -> None:
    """Without a moment the thermostat is told the current time."""
    server_time = Message.parse(login_acknowledgement(1)).message(12)
    assert server_time is not None
    assert server_time.varint(1) == device_time()


def test_login_serial() -> None:
    """The serial number is field 2 of the login."""
    assert login_serial(Message.parse(LOGIN)) == "1234567"


def test_login_serial_without_serial() -> None:
    """A message that is no login, or a login without a serial, has none."""
    assert login_serial(Message.parse(encode_field(1, 1))) is None
    assert login_serial(Message.parse(encode_field(6, b""))) is None
    assert login_serial(Message.parse(encode_field(6, encode_field(2, b"")))) is None
    # Field 6 as a number is not a login at all.
    assert login_serial(Message.parse(encode_field(6, 1))) is None


def test_login_serial_survives_invalid_text() -> None:
    """Bytes that are not UTF-8 do not raise; they can never match a serial."""
    login = Message.parse(encode_field(6, encode_field(2, b"\xff1")))
    assert login_serial(login) == "�1"


def test_login_serial_rejects_malformed_login() -> None:
    """A login that is not a message is an error."""
    with pytest.raises(ValueError, match="protobuf"):
        login_serial(Message.parse(encode_field(6, b"\x00")))


def test_decode_state_converts_the_scale() -> None:
    """22.22 °C held permanently is 4444 on the wire and 2222 in the library."""
    message = Message.parse(
        encode_field(7, encode_field(2, b"Kitchen Floor")) + encode_field(9, _state(3))
    )
    assert decode_state(message) == {
        "RegulationMode": 3,
        "ComfortTemperature": 1666,
        "ManualTemperature": 2222,
        "SetPointTemp": 2222,
        "Room": "Kitchen Floor",
    }


def test_decode_state_reads_each_holds_own_setpoint() -> None:
    """The active setpoint is the one of the hold that the mode selects."""
    # A real permanent hold at 58 °F: the active setpoint is 9.5.1, not 9.4.1.
    permanent = Message.parse(encode_field(9, PERMANENT_STATE))
    assert decode_state(permanent) == {
        "RegulationMode": 3,
        "ComfortTemperature": 1333,
        "ManualTemperature": 1444,
        "SetPointTemp": 1444,
    }

    # A temporary hold uses 9.4.1.
    temporary = Message.parse(encode_field(9, _state(2)))
    assert decode_state(temporary)["SetPointTemp"] == 1666


def test_decode_state_takes_schedule_setpoint_from_device_info() -> None:
    """The schedule's setpoint is not in the state; device info 7.9 has it."""
    state = encode_field(9, _state(1))
    assert "SetPointTemp" not in decode_state(Message.parse(state))

    info = encode_field(7, encode_field(9, 4000))
    assert decode_state(Message.parse(state + info))["SetPointTemp"] == 2000


def test_decode_state_real_messages() -> None:
    """A change at the thermostat and its device info decode as expected."""
    assert decode_state(Message.parse(WALL_STATE)) == {
        "RegulationMode": 2,
        "ComfortTemperature": 2166,
        "ManualTemperature": 1444,
        "SetPointTemp": 2166,
    }
    assert decode_state(Message.parse(WALL_INFO)) == {"SetPointTemp": 2166}


def test_decode_state_with_missing_parts() -> None:
    """Only what the message reports is returned."""
    assert not decode_state(Message.parse(TELEMETRY))

    # A state without a mode, and a hold without a setpoint.
    state = encode_field(4, encode_field(2, 5)) + encode_field(5, encode_field(1, 4000))
    assert decode_state(Message.parse(encode_field(9, state))) == {
        "ManualTemperature": 2000
    }

    # A permanent hold whose setpoint is missing has no active setpoint.
    state = encode_field(2, 3) + encode_field(4, encode_field(1, 4000))
    assert decode_state(Message.parse(encode_field(9, state))) == {
        "RegulationMode": 3,
        "ComfortTemperature": 2000,
    }


def test_decode_state_rejects_malformed_state() -> None:
    """A state that is not a message is an error."""
    with pytest.raises(ValueError, match="protobuf"):
        decode_state(Message.parse(encode_field(9, b"\x00")))


def test_decode_telemetry_heating_flag() -> None:
    """Field 1 is the floor temperature and field 2 the live heating flag."""
    idle = Message.parse(encode_field(15, IDLE))
    heating = Message.parse(encode_field(15, HEATING))
    assert decode_telemetry(idle) == {"Temperature": 2033, "Heating": False}
    assert decode_telemetry(heating) == {"Temperature": 2030, "Heating": True}
    assert decode_telemetry(Message.parse(TELEMETRY)) == {
        "Temperature": 2033,
        "Heating": False,
    }


def test_decode_telemetry_with_missing_parts() -> None:
    """Only what the heartbeat reports is returned."""
    temperature = Message.parse(encode_field(15, encode_field(1, 2100)))
    assert decode_telemetry(temperature) == {"Temperature": 2100}

    heating = Message.parse(encode_field(15, encode_field(2, 1)))
    assert decode_telemetry(heating) == {"Heating": True}

    assert not decode_telemetry(Message.parse(WALL_STATE))


def test_changed_state_permanent_hold_writes_its_own_field() -> None:
    """A permanent hold replaces the permanent setpoint and nothing else.

    From a real temporary hold at 71 °F with 58 °F stored as the permanent
    setpoint, a permanent hold at 22.5 °C must replace the 58 °F. The temporary
    hold's setpoint and end time stay as they were.
    """
    original = _content(WALL_STATE, 9)
    changed = changed_state(original, 3, 2250)

    assert Message.parse(changed).varint(2) == 3
    assert Message.parse(changed).message(5) == Message((Field(1, VARINT, 4500),))
    assert _other_fields(changed, 1, 2, 5) == _other_fields(original, 1, 2, 5)


def test_changed_state_temporary_hold_keeps_end_time() -> None:
    """A temporary hold gets a new setpoint and keeps its end time."""
    original = _content(WALL_STATE, 9)
    changed = changed_state(original, 2, 2000)

    assert Message.parse(changed).varint(2) == 2
    hold = Message.parse(changed).message(4)
    before = Message.parse(original).message(4)
    assert hold is not None
    assert before is not None
    assert hold.varint(1) == 4000
    assert hold.varint(2) == before.varint(2)
    assert _other_fields(changed, 1, 2, 4) == _other_fields(original, 1, 2, 4)


def test_changed_state_schedule_changes_only_the_mode() -> None:
    """Going back to the schedule leaves both setpoints alone."""
    changed = changed_state(PERMANENT_STATE, 1)

    assert Message.parse(changed).varint(2) == 1
    assert _other_fields(changed, 1, 2) == _other_fields(PERMANENT_STATE, 1, 2)
    # The schedule has no setpoint in the state, so a temperature is ignored.
    moment = datetime(2026, 10, 10, tzinfo=UTC)
    assert changed_state(PERMANENT_STATE, 1, 2500, moment) == changed_state(
        PERMANENT_STATE, 1, None, moment
    )


def test_changed_state_copies_the_state_byte_for_byte() -> None:
    """The same mode, setpoint and time give back the thermostat's own bytes."""
    seconds = Message.parse(PERMANENT_STATE).varint(1)
    assert seconds is not None
    moment = EPOCH + timedelta(seconds=seconds)

    assert changed_state(PERMANENT_STATE, 3, 1444, moment) == PERMANENT_STATE


def test_changed_state_adds_a_missing_hold() -> None:
    """A state without the hold gets one, after the fields it already has."""
    original = encode_field(1, 1000) + encode_field(2, 1)
    changed = Message.parse(changed_state(original, 3, 2250))

    assert [field.number for field in changed.fields] == [1, 2, 5]
    assert changed.varint(2) == 3
    assert changed.message(5) == Message((Field(1, VARINT, 4500),))


@freeze_time("2026-10-10 12:00:00")
def test_changed_state_defaults_to_now() -> None:
    """Without a moment the change is timed now."""
    changed = Message.parse(changed_state(PERMANENT_STATE, 1))
    assert changed.varint(1) == device_time()


def test_changed_state_rejects_malformed_state() -> None:
    """A state that is not a message cannot be changed."""
    with pytest.raises(ValueError, match="protobuf"):
        changed_state(b"\x00", 3, 2000)


def test_command() -> None:
    """A command carries the schedule, if there is one, and the state."""
    assert command(101, b"state", b"schedule") == (
        encode_field(1, 101)
        + encode_field(5, 1)
        + encode_field(8, b"schedule")
        + encode_field(9, b"state")
    )
    without = encode_field(1, 102) + encode_field(5, 1) + encode_field(9, b"state")
    assert command(102, b"state") == without
    assert command(102, b"state", b"") == without


def test_response_field() -> None:
    """The two answers after a login are told apart by their only field."""
    configuration = encode_field(1, 1) + encode_field(5, 1) + encode_field(10, b"x")
    support = encode_field(18, b"x")
    assert response_field(Message.parse(configuration)) == FIELD_CONFIGURATION
    assert response_field(Message.parse(support)) == FIELD_SUPPORT


@pytest.mark.parametrize(
    "message",
    [
        b"",
        encode_field(1, 1),
        # Another field next to the answer.
        encode_field(9, b"x") + encode_field(10, b"x"),
        # Both answers in one message.
        encode_field(10, b"x") + encode_field(18, b"x"),
        # The answer twice.
        encode_field(10, b"x") + encode_field(10, b"x"),
        # A number where the answer should be.
        encode_field(18, 1),
        LOGIN,
        TELEMETRY,
    ],
)
def test_response_field_rejects_other_messages(message: bytes) -> None:
    """Anything that is not exactly one of the two answers is refused."""
    assert response_field(Message.parse(message)) is None


def test_describe_real_messages() -> None:
    """The summary shows both holds and the fields that are not interpreted."""
    assert describe(Message.parse(WALL_STATE)) == (
        "seq=406 fields=1,5,9 mode=2 (temporary hold) temporary=21.66C/71.0F "
        "until=2026-10-11 06:00:00Z permanent=14.44C/58.0F 9.3={1: 0} 9.6=0"
    )
    assert describe(Message.parse(WALL_INFO)) == (
        "seq=407 fields=1,5,7 target=21.66C/71.0F"
    )
    assert describe(Message.parse(TELEMETRY)) == (
        "seq=403 fields=1,5,15 floor=20.33C/68.6F heating=0"
    )


def test_describe_never_shows_a_login() -> None:
    """A login holds the account's email address, so only its name is logged."""
    assert describe(Message.parse(LOGIN)) == "login"


def test_describe_uploads_and_unknown_modes() -> None:
    """The kind of an upload and an unknown mode are shown as they are."""
    upload = encode_field(1, 31) + encode_field(2, 4) + encode_field(5, 1)
    assert describe(Message.parse(upload)) == "seq=31 fields=1,2,5 kind=4"

    odd = encode_field(2, 9) + encode_field(
        3, encode_field(1, 0) + encode_field(2, b"\x01\x02")
    )
    assert describe(Message.parse(encode_field(9, odd))) == (
        "seq=None fields=9 mode=9 (unknown) 9.3={1: 0, 2: 0102}"
    )


def test_describe_with_missing_parts() -> None:
    """Parts that a message lacks are left out of the summary."""
    # A temporary hold without an end time.
    assert describe(Message.parse(encode_field(9, _state(2)))) == (
        "seq=None fields=9 mode=2 (temporary hold) temporary=16.66C/62.0F "
        "permanent=22.22C/72.0F 9.6=0"
    )
    # A state without a mode; a field that occurs twice is shown once.
    state = encode_field(6, 0) + encode_field(6, 1)
    assert describe(Message.parse(encode_field(9, state))) == (
        "seq=None fields=9 mode=None (unknown) 9.6=0"
    )
    # Device info without a setpoint, and a heartbeat without values.
    info = encode_field(7, encode_field(2, b"Hall"))
    assert describe(Message.parse(info)) == "seq=None fields=7"
    assert describe(Message.parse(encode_field(15, b""))) == "seq=None fields=15"
    floor = encode_field(15, encode_field(1, 2100))
    assert describe(Message.parse(floor)) == "seq=None fields=15 floor=21.00C/69.8F"


def test_describe_survives_malformed_parts() -> None:
    """Logging a message never raises, whatever the thermostat sent."""
    # A field of the state that is not a message is shown as hexadecimal.
    state = encode_field(2, 1) + encode_field(3, b"\xff")
    assert describe(Message.parse(encode_field(9, state))) == (
        "seq=None fields=9 mode=1 (schedule) 9.3=ff"
    )
    # A state that is not a message at all.
    assert describe(Message.parse(encode_field(9, b"\x00"))) == (
        "seq=None fields=9 undecodable"
    )
