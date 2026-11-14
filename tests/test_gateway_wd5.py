"""Test the WD5 device messages of the gateway.

The login is a frame captured from a real OWD5 thermostat with firmware
1013W220 by patricknitsch/ioBroker.schlueter-thermostat (MIT). The messages
marked as logged have the fields and values that project logged from the same
thermostat and from OJ's gateway; their bytes are rebuilt from those logs.

Setpoints on the wire are in 1/200 °C while the library uses 1/100 °C, and the
measured temperatures are in 1/100 °C on both sides. Getting that wrong writes
half or twice the intended temperature, so it is pinned here.
"""

import json
from datetime import UTC, date, datetime, time, timedelta, timezone

import pytest
from freezegun import freeze_time

from ojmicroline_thermostat.const import (
    REGULATION_BOOST,
    REGULATION_COMFORT,
    REGULATION_ECO,
    REGULATION_FROST_PROTECTION,
    REGULATION_MANUAL,
    REGULATION_SCHEDULE,
    REGULATION_VACATION,
)
from ojmicroline_thermostat.gateway.protobuf import (
    Field,
    Message,
    decode_frame,
    encode_field,
    from_wire,
    to_wire,
)
from ojmicroline_thermostat.gateway.wd5 import (
    NOT_SET,
    Login,
    Vacation,
    answer,
    changed_state,
    command,
    decode_date,
    decode_date_time,
    decode_login,
    decode_schedule,
    decode_state,
    decode_telemetry,
    default_state,
    describe,
    encode_date,
    encode_date_time,
    is_answer,
    local_in,
    login_answer,
    upload_request,
    vacation_mode,
    wants_answer,
)
from ojmicroline_thermostat.gateway.wg4 import EPOCH, device_time
from ojmicroline_thermostat.models import Schedule, Thermostat

from . import load_fixtures

# The thermostat is at UTC+1, as its device info says (7.3 = 3600).
ZONE = timezone(timedelta(hours=1))


def _day(year: int, month: int, day: int) -> bytes:
    """Build a day as the thermostat has it."""
    return encode_field(1, year) + encode_field(2, month) + encode_field(3, day)


def _end(day: bytes, hour: int = 0, minute: int = 0, second: int = 0) -> bytes:
    """Build an end as the thermostat has it: a day and a time."""
    clock = encode_field(1, hour) + encode_field(2, minute) + encode_field(3, second)
    return encode_field(1, day) + encode_field(2, clock)


NO_DAY = _day(1970, 1, 1)
NO_END = _end(NO_DAY)

# The first frame of a real OWD5 thermostat: 1 = 1, 4 = 1 and the login in
# field 5, which also has a 5.4 = 1 that is not understood.
LOGIN = decode_frame(
    bytes.fromhex(
        "0825080120012a1f0a073130323134313010031a08313031335732323020012a060806100e1833"
    )
)

# Logged: the upload of the thermostat while it follows its schedule.
UPLOAD_STATE = (
    encode_field(1, 847837681)
    + encode_field(2, 1)
    + encode_field(5, encode_field(1, 0))
    + encode_field(8, 1)
    + encode_field(9, 0)
)
UPLOAD = (
    encode_field(1, 13)
    + encode_field(4, 1)
    + encode_field(
        7, encode_field(1, 847837681) + encode_field(2, b"Bad") + encode_field(3, 3600)
    )
    + encode_field(9, UPLOAD_STATE)
)

# Logged: a heartbeat at 19.73 °C in the room and 33.01 °C in the floor.
HEARTBEAT = (
    encode_field(1, 14)
    + encode_field(4, 1)
    + encode_field(
        6,
        encode_field(1, 847862621)
        + encode_field(2, 1973)
        + encode_field(3, 3301)
        + encode_field(4, 1)
        + encode_field(5, 1),
    )
)

# Logged: the command of OJ's gateway for the manual mode at 23 °C.
MANUAL_STATE = (
    encode_field(1, 847862879)
    + encode_field(2, 3)
    + encode_field(3, encode_field(1, 4600) + encode_field(2, NO_END))
    + encode_field(4, encode_field(1, 4600))
    + encode_field(7, encode_field(1, 1000))
    + encode_field(8, 1)
    + encode_field(9, 0)
)
MANUAL_COMMAND = encode_field(1, 3) + encode_field(4, 1) + encode_field(9, MANUAL_STATE)
MANUAL_MOMENT = EPOCH + timedelta(seconds=847862879)

# What a thermostat answers a message with: here the request for its upload.
ACKNOWLEDGEMENT = encode_field(1, 0) + encode_field(3, 1)


def _vacation_state(vacation: bytes) -> bytes:
    """Build a complete state as OJ's gateway sent it, around a vacation."""
    return (
        encode_field(1, 847890037)
        + encode_field(2, 1)
        + encode_field(3, encode_field(1, 4600) + encode_field(2, NO_END))
        + encode_field(4, encode_field(1, 0))
        + encode_field(5, vacation)
        + encode_field(6, encode_field(1, NO_END))
        + encode_field(7, encode_field(1, 1000))
        + encode_field(8, 1)
        + encode_field(9, 0)
    )


NO_VACATION_STATE = _vacation_state(
    encode_field(1, 0)
    + encode_field(2, NO_DAY)
    + encode_field(3, NO_DAY)
    + encode_field(4, 1000)
)
# Logged: the state OJ's gateway sent for a vacation on 1 and 2 January 2027.
VACATION_STATE = _vacation_state(
    encode_field(1, 1)
    + encode_field(2, _day(2027, 1, 1))
    + encode_field(3, _day(2027, 1, 2))
    + encode_field(4, 1000)
)
VACATION_MOMENT = EPOCH + timedelta(seconds=847890037)


def _event(clock: bytes, temperature: int, active: int, next_day: int) -> bytes:
    """Build an event of the schedule."""
    return (
        encode_field(1, clock)
        + encode_field(2, temperature)
        + encode_field(3, active)
        + encode_field(4, next_day)
    )


def _clock(hour: int, minute: int, second: int = 0) -> bytes:
    """Build the time of an event of the schedule."""
    return encode_field(1, hour) + encode_field(2, minute) + encode_field(3, second)


# No schedule of a real thermostat was published. This one follows the layout
# that patricknitsch describes: Monday with two events, Tuesday with none.
MONDAY = (
    encode_field(1, 1)
    + encode_field(2, _event(_clock(6, 30), 2350, 1, 0))
    + encode_field(2, _event(_clock(0, 15, 30), 1500, 0, 1))
)
SCHEDULE = (
    encode_field(1, 15)
    + encode_field(4, 1)
    + encode_field(
        8,
        encode_field(1, 847837681)
        + encode_field(
            2, encode_field(1, MONDAY) + encode_field(1, encode_field(1, 2))
        ),
    )
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


def _decoded(state: bytes) -> dict[str, object]:
    """Decode a state the way it arrives: in field 9 of a message."""
    return decode_state(Message.parse(encode_field(9, state)))


def test_setpoint_scale() -> None:
    """A setpoint on the wire is twice its value in 1/100 °C."""
    assert from_wire(4600) == 2300
    assert from_wire(1000) == 500
    assert to_wire(2300) == 4600
    assert to_wire(2450) == 4900
    assert to_wire(0) == 0


def test_decode_login_real_frame() -> None:
    """The login of a real thermostat tells its serial, firmware and clock."""
    assert decode_login(Message.parse(LOGIN)) == Login(
        serial="1021410",
        device_type=3,
        firmware="1013W220",
        clock=time(6, 14, 51),
    )


@pytest.mark.parametrize(
    "message",
    [
        b"",
        encode_field(1, 1),
        # The login of a WG4 thermostat: its field 5 is a number.
        encode_field(1, 29) + encode_field(5, 1) + encode_field(6, b""),
        # A login without a serial number, or with one that is no text.
        encode_field(5, b""),
        encode_field(5, encode_field(1, b"")),
        encode_field(5, encode_field(1, b"\xff1")),
        encode_field(5, encode_field(1, 1021410)),
        HEARTBEAT,
    ],
)
def test_decode_login_refuses_other_messages(message: bytes) -> None:
    """Anything without a serial number in field 5 is not a login."""
    assert decode_login(Message.parse(message)) is None


def test_decode_login_with_missing_parts() -> None:
    """A part that is missing or not what it should be is None."""
    serial = encode_field(1, b"1000001")
    assert decode_login(Message.parse(encode_field(5, serial))) == Login(
        serial="1000001", device_type=None, firmware=None, clock=None
    )

    # A firmware that is not text, and a clock that is not a time.
    odd = serial + encode_field(3, b"\x00\x01") + encode_field(5, encode_field(1, 24))
    assert decode_login(Message.parse(encode_field(5, odd))) == Login(
        serial="1000001", device_type=None, firmware=None, clock=None
    )

    # A number of the clock that is missing is zero, as in protobuf.
    early = serial + encode_field(5, encode_field(2, 5))
    login = decode_login(Message.parse(encode_field(5, early)))
    assert login is not None
    assert login.clock == time(0, 5, 0)


def test_decode_login_rejects_malformed_login() -> None:
    """A login that is not a message is an error."""
    with pytest.raises(ValueError, match="protobuf"):
        decode_login(Message.parse(encode_field(5, b"\x00")))


def test_is_answer() -> None:
    """An acknowledgement has field 3, and no field 4."""
    assert is_answer(Message.parse(ACKNOWLEDGEMENT))
    assert is_answer(Message.parse(encode_field(1, 101) + encode_field(3, 1)))

    assert not is_answer(Message.parse(UPLOAD))
    assert not is_answer(Message.parse(HEARTBEAT))
    assert not is_answer(Message.parse(encode_field(1, 5)))
    # The answer to a login also wants an answer, so it is not just that.
    assert not is_answer(Message.parse(login_answer(1)))


def test_wants_answer() -> None:
    """A message wants an answer if its field 4 is 1 and it is not an answer."""
    assert wants_answer(Message.parse(LOGIN))
    assert wants_answer(Message.parse(UPLOAD))
    assert wants_answer(Message.parse(HEARTBEAT))
    assert wants_answer(Message.parse(command(101, MANUAL_STATE)))
    assert wants_answer(Message.parse(upload_request()))

    assert not wants_answer(Message.parse(ACKNOWLEDGEMENT))
    assert not wants_answer(Message.parse(encode_field(1, 5)))
    assert not wants_answer(Message.parse(encode_field(1, 5) + encode_field(4, 0)))
    assert not wants_answer(Message.parse(login_answer(1)))
    assert not wants_answer(Message.parse(answer(13)))


def test_answer() -> None:
    """An answer repeats the sequence number and tells the server time."""
    assert answer(13, MANUAL_MOMENT) == (
        encode_field(1, 13)
        + encode_field(3, 1)
        + encode_field(12, encode_field(1, 847862879))
    )
    assert answer(13, MANUAL_MOMENT) == bytes.fromhex("080d1801620608dfb8a59403")


def test_login_answer() -> None:
    """The answer to a login is an answer that wants one itself."""
    assert login_answer(1, MANUAL_MOMENT) == (
        encode_field(1, 1)
        + encode_field(3, 1)
        + encode_field(4, 1)
        + encode_field(12, encode_field(1, 847862879))
    )


def test_upload_request() -> None:
    """The request for the full upload has sequence number 0 and 10.2 = 1."""
    assert upload_request(MANUAL_MOMENT) == (
        encode_field(1, 0)
        + encode_field(4, 1)
        + encode_field(10, encode_field(1, 847862879) + encode_field(2, 1))
    )


def test_server_time_counts_in_utc() -> None:
    """The server time is the same moment in every time zone."""
    elsewhere = MANUAL_MOMENT.astimezone(ZONE)
    assert answer(13, elsewhere) == answer(13, MANUAL_MOMENT)
    assert login_answer(1, elsewhere) == login_answer(1, MANUAL_MOMENT)
    assert upload_request(elsewhere) == upload_request(MANUAL_MOMENT)


@freeze_time("2026-11-13 13:10:00")
def test_answers_default_to_now() -> None:
    """Without a moment the thermostat is told the current time."""
    for message, number in (
        (answer(13), 12),
        (login_answer(1), 12),
        (upload_request(), 10),
    ):
        server_time = Message.parse(message).message(number)
        assert server_time is not None
        assert server_time.varint(1) == device_time()


def test_decode_telemetry_logged_heartbeat() -> None:
    """Both temperatures are in 1/100 °C on the wire, unlike a setpoint."""
    assert decode_telemetry(Message.parse(HEARTBEAT)) == {
        "RoomTemperature": 1973,
        "FloorTemperature": 3301,
        "Heating": True,
    }


def test_decode_telemetry_with_missing_parts() -> None:
    """Only what the heartbeat reports is returned."""
    room = Message.parse(encode_field(6, encode_field(2, 2100)))
    assert decode_telemetry(room) == {"RoomTemperature": 2100}

    idle = Message.parse(encode_field(6, encode_field(3, 2500) + encode_field(4, 0)))
    assert decode_telemetry(idle) == {"FloorTemperature": 2500, "Heating": False}

    assert not decode_telemetry(Message.parse(encode_field(6, b"")))
    assert not decode_telemetry(Message.parse(UPLOAD))


def test_decode_telemetry_rejects_malformed_heartbeat() -> None:
    """A heartbeat that is not a message is an error."""
    with pytest.raises(ValueError, match="protobuf"):
        decode_telemetry(Message.parse(encode_field(6, b"\x00")))


def test_decode_state_logged_upload() -> None:
    """The upload gives the mode, the vacation flag, the name and the zone."""
    assert decode_state(Message.parse(UPLOAD)) == {
        "RegulationMode": 1,
        "VacationEnabled": False,
        "ThermostatName": "Bad",
        "TimeZone": 3600,
    }


def test_decode_state_converts_the_scale() -> None:
    """23 °C is 4600 on the wire and 2300 in the library."""
    assert decode_state(Message.parse(MANUAL_COMMAND)) == {
        "RegulationMode": 3,
        "ComfortSetpoint": 2300,
        "ComfortEndTime": NOT_SET,
        "ManualModeSetpoint": 2300,
        "FrostProtectionTemperature": 500,
    }


def test_decode_state_logged_vacation() -> None:
    """A complete state gives every field of the state."""
    assert _decoded(VACATION_STATE) == {
        "RegulationMode": 1,
        "ComfortSetpoint": 2300,
        "ComfortEndTime": NOT_SET,
        "ManualModeSetpoint": 0,
        "VacationEnabled": True,
        "VacationBeginDay": "2027-01-01T00:00:00",
        "VacationEndDay": "2027-01-02T00:00:00",
        "VacationTemperature": 500,
        "BoostEndTime": NOT_SET,
        "FrostProtectionTemperature": 500,
    }


def test_decode_state_with_missing_parts() -> None:
    """Only what the message reports is returned."""
    assert not decode_state(Message.parse(HEARTBEAT))
    assert not decode_state(Message.parse(encode_field(9, b"")))

    # A boost, and the mode that goes with it.
    boost = encode_field(2, 8) + encode_field(
        6, encode_field(1, _end(_day(2026, 11, 13), 15, 10, 5))
    )
    assert _decoded(boost) == {
        "RegulationMode": 8,
        "BoostEndTime": "2026-11-13T15:10:05",
    }

    # A comfort hold without an end, and a vacation with nothing but a day.
    partial = encode_field(3, encode_field(1, 4900)) + encode_field(
        5, encode_field(3, _day(2027, 2, 28))
    )
    assert _decoded(partial) == {
        "ComfortSetpoint": 2450,
        "VacationEndDay": "2027-02-28T00:00:00",
    }

    # Device info with nothing but the load of the floor.
    load = Message.parse(encode_field(7, encode_field(17, 350)))
    assert decode_state(load) == {"LoadWatts": 350}


@pytest.mark.parametrize(
    "end",
    [
        b"",
        # A day without a time, and a time without a day.
        encode_field(1, _day(2026, 11, 13)),
        encode_field(2, _clock(15, 10)),
        # Days and times that do not exist.
        _end(_day(2026, 13, 1)),
        _end(_day(2026, 11, 13), 24),
        _end(_day(2026, 11, 13), 2**40),
    ],
)
def test_decode_state_leaves_out_an_end_that_is_no_date(end: bytes) -> None:
    """An end that is no date and time is not reported, and is no error."""
    state = encode_field(3, encode_field(2, end)) + encode_field(
        6, encode_field(1, end)
    )
    assert not _decoded(state)


@pytest.mark.parametrize(
    "day",
    [
        b"",
        _day(0, 0, 0),
        _day(2026, 13, 1),
        _day(2026, 2, 30),
        _day(10000, 1, 1),
        _day(2**40, 1, 1),
    ],
)
def test_decode_state_leaves_out_a_day_that_is_no_date(day: bytes) -> None:
    """A day that does not exist is not reported, and is no error."""
    state = encode_field(5, encode_field(2, day) + encode_field(3, day))
    assert not _decoded(state)
    assert not _decoded(encode_field(3, encode_field(2, _end(day, 12))))


def test_decode_state_survives_invalid_text() -> None:
    """A name that is not UTF-8 does not raise."""
    info = Message.parse(encode_field(7, encode_field(2, b"\xffBad")))
    assert decode_state(info) == {"ThermostatName": "�Bad"}

    # An empty name is a name, and a name that is a number is none.
    empty = Message.parse(encode_field(7, encode_field(2, b"")))
    assert decode_state(empty) == {"ThermostatName": ""}
    assert not decode_state(Message.parse(encode_field(7, encode_field(2, 5))))


@pytest.mark.parametrize(
    "message",
    [
        encode_field(9, b"\x00"),
        encode_field(7, b"\x00"),
        encode_field(9, encode_field(3, b"\x00")),
        encode_field(9, encode_field(3, encode_field(2, b"\x00"))),
        encode_field(9, encode_field(5, encode_field(2, b"\x00"))),
        encode_field(9, encode_field(6, encode_field(1, encode_field(1, b"\x00")))),
    ],
)
def test_decode_state_rejects_malformed_state(message: bytes) -> None:
    """A state with a part that is not a message is an error."""
    with pytest.raises(ValueError, match="protobuf"):
        decode_state(Message.parse(message))


def test_decoded_fields_are_those_of_the_cloud() -> None:
    """What is decoded can be laid over the thermostat JSON of the cloud API."""
    data = json.loads(load_fixtures("wd5_thermostat.json"))
    everything = encode_field(
        7, encode_field(2, b"Bad") + encode_field(3, 3600) + encode_field(17, 350)
    ) + encode_field(9, VACATION_STATE)
    fields = decode_state(Message.parse(everything))
    fields |= decode_telemetry(Message.parse(HEARTBEAT))
    schedule = decode_schedule(Message.parse(SCHEDULE))
    assert schedule is not None

    # The load of the floor is the one value the cloud API does not have.
    assert set(fields) - set(data) == {"LoadWatts"}
    assert len(fields) == 16
    assert set(schedule) <= set(data["Schedule"])
    assert set(schedule["Days"][0]) == set(data["Schedule"]["Days"][0])
    assert set(schedule["Days"][0]["Events"][0]) == (
        set(data["Schedule"]["Days"][0]["Events"][0]) - {"ScheduleType"}
    )

    data.update(fields)
    data["Schedule"] = schedule
    thermostat = Thermostat.from_wd5_json(data)
    assert thermostat.name == "Bad"
    assert thermostat.heating is True
    assert thermostat.temperature_room == 1973
    assert thermostat.temperature_floor == 3301
    assert thermostat.regulation_mode == REGULATION_SCHEDULE
    assert thermostat.comfort_temperature == 2300
    assert thermostat.manual_temperature == 0
    assert thermostat.frost_protection_temperature == 500
    assert thermostat.vacation_mode is True
    assert thermostat.vacation_temperature == 500
    assert thermostat.vacation_begin_time is not None
    assert thermostat.vacation_begin_time.utcoffset() == timedelta(hours=1)
    # An end that is not set is still a date for the model.
    assert thermostat.boost_end_time is not None
    assert thermostat.boost_end_time.year < 1971
    assert thermostat.schedule == schedule


def test_decode_schedule() -> None:
    """The schedule has the shape of the Schedule of the cloud API."""
    assert decode_schedule(Message.parse(SCHEDULE)) == {
        "Days": [
            {
                "WeekDayGrpNo": 1,
                "Events": [
                    {
                        "Clock": "06:30:00",
                        "Temperature": 2350,
                        "Active": True,
                        "EventIsOnNextDay": False,
                    },
                    {
                        "Clock": "00:15:30",
                        "Temperature": 1500,
                        "Active": False,
                        "EventIsOnNextDay": True,
                    },
                ],
            },
            {"WeekDayGrpNo": 2, "Events": []},
        ]
    }


def test_decode_schedule_fits_the_model() -> None:
    """The Schedule model reads a decoded schedule."""
    decoded = decode_schedule(Message.parse(SCHEDULE))
    assert decoded is not None
    schedule = Schedule.from_json(decoded)

    # Monday is day 0 of the model, and only its active event counts.
    assert [event.temperature for event in schedule.days[0]] == [2350]
    assert schedule.days[1] == []


def test_decode_schedule_without_schedule() -> None:
    """A message without a schedule, or a schedule without a week, has none."""
    assert decode_schedule(Message.parse(UPLOAD)) is None
    assert decode_schedule(Message.parse(encode_field(8, b""))) is None
    assert decode_schedule(Message.parse(encode_field(8, encode_field(1, 5)))) is None
    assert decode_schedule(Message.parse(encode_field(8, encode_field(2, 5)))) is None

    # A week without days is a schedule without days.
    empty = Message.parse(encode_field(8, encode_field(2, b"")))
    assert decode_schedule(empty) == {"Days": []}


def test_decode_schedule_with_missing_parts() -> None:
    """Only what the thermostat sent is returned, and what is no day is skipped."""
    day = (
        # An event that is a number is not an event.
        encode_field(2, 7)
        # An event with a clock that is not a time, and one with only a flag.
        + encode_field(2, encode_field(1, _clock(24, 0)) + encode_field(2, 2000))
        + encode_field(2, encode_field(3, 1))
        + encode_field(2, b"")
    )
    week = (
        # A day that is a number, and one that is a fixed32, are not days.
        encode_field(1, 3)
        + bytes([1 << 3 | 5, 1, 2, 3, 4])
        + encode_field(1, day)
        # Another field of the week.
        + encode_field(2, b"")
    )
    assert decode_schedule(Message.parse(encode_field(8, encode_field(2, week)))) == {
        "Days": [{"Events": [{"Temperature": 2000}, {"Active": True}, {}]}]
    }


@pytest.mark.parametrize(
    "schedule",
    [
        b"\x00",
        encode_field(2, b"\x00"),
        encode_field(2, encode_field(1, b"\x00")),
        encode_field(2, encode_field(1, encode_field(2, b"\x00"))),
        encode_field(2, encode_field(1, encode_field(2, encode_field(1, b"\x00")))),
    ],
)
def test_decode_schedule_rejects_malformed_schedule(schedule: bytes) -> None:
    """A schedule with a part that is not a message is an error."""
    with pytest.raises(ValueError, match="protobuf"):
        decode_schedule(Message.parse(encode_field(8, schedule)))


def test_dates_as_the_cloud_writes_them() -> None:
    """A day and an end decode to the local time of the thermostat."""
    day = _day(2027, 1, 2)
    assert decode_date(Message.parse(day)) == "2027-01-02T00:00:00"
    assert decode_date_time(Message.parse(_end(day, 9, 5, 7))) == "2027-01-02T09:05:07"
    # A year before 1000 keeps its four digits.
    assert decode_date(Message.parse(_day(970, 1, 2))) == "0970-01-02T00:00:00"

    assert decode_date(Message.parse(NO_DAY)) == NOT_SET
    assert decode_date_time(Message.parse(NO_END)) == NOT_SET

    assert decode_date(None) is None
    assert decode_date(Message.parse(b"")) is None
    assert decode_date_time(None) is None
    assert decode_date_time(Message.parse(b"")) is None


def test_encode_dates() -> None:
    """A day and an end are encoded as the thermostat has them."""
    assert encode_date(date(2027, 1, 2)) == _day(2027, 1, 2)
    assert encode_date(None) == NO_DAY
    assert encode_date_time(None) == NO_END

    local = datetime(2026, 11, 13, 15, 10, 5, tzinfo=ZONE)
    assert encode_date_time(local) == _end(_day(2026, 11, 13), 15, 10, 5)
    # The date and the time are written as they are, whatever the time zone.
    assert encode_date_time(local.replace(tzinfo=UTC)) == encode_date_time(local)
    # A moment is a day as well.
    assert encode_date(local) == _day(2026, 11, 13)


def test_local_in() -> None:
    """The local time of the thermostat is UTC plus its time zone."""
    moment = datetime(2026, 11, 13, 13, 10, tzinfo=UTC)
    assert local_in(60, 3600, moment) == datetime(2026, 11, 13, 14, 10, tzinfo=UTC)
    assert local_in(60, 3600, moment).isoformat() == "2026-11-13T15:10:00+01:00"
    assert local_in(0, -18000, moment).isoformat() == "2026-11-13T08:10:00-05:00"
    # Whatever the time zone of the moment itself.
    assert local_in(0, 0, moment.astimezone(ZONE)).isoformat() == (
        "2026-11-13T13:10:00+00:00"
    )


@freeze_time("2026-11-13 23:30:00")
def test_local_in_defaults_to_now() -> None:
    """Without a moment the current time is used."""
    assert local_in(45, 3600).isoformat() == "2026-11-14T01:15:00+01:00"


@pytest.mark.parametrize("time_zone", [86400, -86400, 2**64 - 18000])
def test_local_in_rejects_an_impossible_time_zone(time_zone: int) -> None:
    """A time zone is less than a day away from UTC, whatever was decoded."""
    with pytest.raises(ValueError, match="a day or more away from UTC"):
        local_in(0, time_zone)
    assert local_in(0, 86399, MANUAL_MOMENT).utcoffset() == timedelta(seconds=86399)


def test_default_state_matches_the_cloud() -> None:
    """The default state is byte for byte a state that OJ's gateway sent."""
    known = {"ComfortSetpoint": 2300}
    assert default_state(known, VACATION_MOMENT) == NO_VACATION_STATE


@freeze_time("2026-11-13 13:10:00")
def test_default_state_without_known_values() -> None:
    """What is not known gets a default, and the change is timed now."""
    for state in (default_state(), default_state({})):
        assert Message.parse(state).varint(1) == device_time()
        assert _decoded(state) == {
            "RegulationMode": 1,
            "ComfortSetpoint": 2100,
            "ComfortEndTime": NOT_SET,
            "ManualModeSetpoint": 0,
            "VacationEnabled": False,
            "VacationBeginDay": NOT_SET,
            "VacationEndDay": NOT_SET,
            "VacationTemperature": 500,
            "BoostEndTime": NOT_SET,
            "FrostProtectionTemperature": 500,
        }


def test_default_state_with_known_values() -> None:
    """Every setpoint that is known is used, and nothing else is."""
    known = {
        "RegulationMode": 3,
        "ComfortSetpoint": None,
        "ManualModeSetpoint": 2150,
        "VacationTemperature": 700,
        "FrostProtectionTemperature": 600,
        "VacationEnabled": True,
    }
    decoded = _decoded(default_state(known, VACATION_MOMENT))
    assert decoded["RegulationMode"] == 1
    assert decoded["ComfortSetpoint"] == 2100
    assert decoded["ManualModeSetpoint"] == 2150
    assert decoded["VacationTemperature"] == 700
    assert decoded["FrostProtectionTemperature"] == 600
    assert decoded["VacationEnabled"] is False

    # The thermostat JSON of the cloud API has all four setpoints.
    cloud = json.loads(load_fixtures("wd5_thermostat.json"))
    decoded = _decoded(default_state(cloud, VACATION_MOMENT))
    assert decoded["ComfortSetpoint"] == cloud["ComfortSetpoint"] == 2000
    assert decoded["ManualModeSetpoint"] == cloud["ManualModeSetpoint"] == 2350
    assert decoded["VacationTemperature"] == cloud["VacationTemperature"] == 500
    assert decoded["FrostProtectionTemperature"] == 500


def test_changed_state_vacation_matches_the_cloud() -> None:
    """A vacation is stored byte for byte as OJ's gateway stored it."""
    vacation = Vacation(enabled=True, begin=date(2027, 1, 1), end=date(2027, 1, 2))
    changed = changed_state(
        NO_VACATION_STATE, REGULATION_SCHEDULE, None, VACATION_MOMENT, vacation=vacation
    )
    assert changed == VACATION_STATE

    # The setpoint of the vacation stays as the thermostat has it.
    assert _decoded(changed)["VacationTemperature"] == 500


def test_changed_state_vacation_keeps_the_mode() -> None:
    """Without a mode a vacation leaves the mode alone, and can be removed."""
    changed = changed_state(VACATION_STATE, vacation=Vacation(enabled=False))
    assert _other_fields(changed, 1, 5) == _other_fields(VACATION_STATE, 1, 5)
    assert _content(changed, 5) == _content(NO_VACATION_STATE, 5)

    # A state without a vacation gets one, between the fields it has.
    moments = Vacation(
        enabled=True,
        begin=datetime(2027, 1, 1, 12, tzinfo=ZONE),
        end=datetime(2027, 1, 2, 12, tzinfo=ZONE),
    )
    changed = changed_state(MANUAL_STATE, vacation=moments)
    assert [field.number for field in Message.parse(changed).fields] == [
        1,
        2,
        3,
        4,
        5,
        7,
        8,
        9,
    ]
    assert _other_fields(changed, 1, 5) == _other_fields(MANUAL_STATE, 1, 5)
    assert _content(changed, 5) == (
        encode_field(1, 1)
        + encode_field(2, _day(2027, 1, 1))
        + encode_field(3, _day(2027, 1, 2))
    )


def test_changed_state_comfort_from_a_command_of_the_cloud() -> None:
    """A comfort hold gets its setpoint and end; the rest stays as it was."""
    end = local_in(60, 3600, MANUAL_MOMENT)
    changed = changed_state(MANUAL_STATE, REGULATION_COMFORT, 2450, end=end)

    sent = Message.parse(command(101, changed))
    assert sent.varint(1) == 101
    assert sent.varint(4) == 1
    assert decode_state(sent) == {
        "RegulationMode": 2,
        "ComfortSetpoint": 2450,
        "ComfortEndTime": f"{end:%Y-%m-%dT%H:%M:%S}",
        "ManualModeSetpoint": 2300,
        "FrostProtectionTemperature": 500,
    }
    assert _other_fields(changed, 1, 2, 3) == _other_fields(MANUAL_STATE, 1, 2, 3)


def test_changed_state_comfort_keeps_what_is_not_given() -> None:
    """Without a setpoint or an end, a comfort hold keeps the one it has."""
    end = datetime(2026, 11, 13, 18, 0, tzinfo=ZONE)

    changed = changed_state(MANUAL_STATE, REGULATION_COMFORT, 2000)
    assert _content(changed, 3) == encode_field(1, 4000) + encode_field(2, NO_END)

    changed = changed_state(MANUAL_STATE, REGULATION_COMFORT, end=end)
    assert _content(changed, 3) == encode_field(1, 4600) + encode_field(
        2, _end(_day(2026, 11, 13), 18)
    )

    # With neither of them only the time and the mode change.
    changed = changed_state(MANUAL_STATE, REGULATION_COMFORT)
    assert Message.parse(changed).varint(2) == 2
    assert _other_fields(changed, 1, 2) == _other_fields(MANUAL_STATE, 1, 2)
    partial = changed_state(UPLOAD_STATE, REGULATION_COMFORT)
    assert [field.number for field in Message.parse(partial).fields] == [1, 2, 5, 8, 9]


def test_changed_state_manual_writes_its_own_field() -> None:
    """A manual setpoint goes into 9.4.1, and not into the comfort hold."""
    changed = changed_state(default_state(), REGULATION_MANUAL, 2150)
    decoded = _decoded(changed)
    assert decoded["RegulationMode"] == 3
    assert decoded["ManualModeSetpoint"] == 2150
    assert decoded["ComfortSetpoint"] == 2100

    # Without a setpoint the thermostat keeps the one it has.
    kept = changed_state(MANUAL_STATE, REGULATION_MANUAL, None, MANUAL_MOMENT)
    assert kept == MANUAL_STATE


def test_changed_state_adds_what_a_state_does_not_have() -> None:
    """A state without the field of a mode gets it, between the others."""
    end = datetime(2026, 11, 13, 15, 10, 5, tzinfo=ZONE)

    manual = Message.parse(changed_state(UPLOAD_STATE, REGULATION_MANUAL, 2150))
    assert [field.number for field in manual.fields] == [1, 2, 4, 5, 8, 9]
    assert manual.message(4) == Message.parse(encode_field(1, 4300))

    comfort = changed_state(UPLOAD_STATE, REGULATION_COMFORT, 2150, end=end)
    assert _content(comfort, 3) == encode_field(1, 4300) + encode_field(
        2, _end(_day(2026, 11, 13), 15, 10, 5)
    )

    boost = changed_state(UPLOAD_STATE, REGULATION_BOOST, end=end)
    assert [field.number for field in Message.parse(boost).fields] == [1, 2, 5, 6, 8, 9]
    assert _decoded(boost) == {
        "RegulationMode": 8,
        "VacationEnabled": False,
        "BoostEndTime": "2026-11-13T15:10:05",
    }


def test_changed_state_boost() -> None:
    """A boost gets its end, and nothing else changes."""
    end = datetime(2026, 11, 13, 15, 10, 5, tzinfo=ZONE)
    changed = changed_state(NO_VACATION_STATE, REGULATION_BOOST, end=end)

    assert _decoded(changed)["RegulationMode"] == 8
    assert _decoded(changed)["BoostEndTime"] == "2026-11-13T15:10:05"
    assert _other_fields(changed, 1, 2, 6) == _other_fields(NO_VACATION_STATE, 1, 2, 6)

    # Without an end only the time and the mode change.
    changed = changed_state(NO_VACATION_STATE, REGULATION_BOOST)
    assert _other_fields(changed, 1, 2) == _other_fields(NO_VACATION_STATE, 1, 2)


@pytest.mark.parametrize(
    "mode",
    [
        None,
        REGULATION_SCHEDULE,
        REGULATION_VACATION,
        REGULATION_FROST_PROTECTION,
        REGULATION_BOOST,
        REGULATION_ECO,
    ],
)
def test_changed_state_ignores_a_setpoint_outside_its_modes(mode: int | None) -> None:
    """Only the comfort and the manual mode have a setpoint to change."""
    changed = changed_state(NO_VACATION_STATE, mode, 2500, VACATION_MOMENT)
    assert changed == changed_state(NO_VACATION_STATE, mode, None, VACATION_MOMENT)
    expected = 1 if mode is None else mode
    assert Message.parse(changed).varint(2) == expected
    assert _other_fields(changed, 2) == _other_fields(NO_VACATION_STATE, 2)


@pytest.mark.parametrize(
    "mode",
    [
        None,
        REGULATION_SCHEDULE,
        REGULATION_MANUAL,
        REGULATION_VACATION,
        REGULATION_FROST_PROTECTION,
        REGULATION_ECO,
    ],
)
def test_changed_state_ignores_an_end_outside_its_modes(mode: int | None) -> None:
    """Only a comfort hold and a boost have an end to change."""
    end = datetime(2026, 11, 13, 15, 10, 5, tzinfo=ZONE)
    changed = changed_state(NO_VACATION_STATE, mode, None, VACATION_MOMENT, end=end)
    assert changed == changed_state(NO_VACATION_STATE, mode, None, VACATION_MOMENT)


def test_changed_state_copies_the_state_byte_for_byte() -> None:
    """The same values and time give back the bytes of the state."""
    vacation = Vacation(enabled=True, begin=date(2027, 1, 1), end=date(2027, 1, 2))
    same = changed_state(
        VACATION_STATE, REGULATION_SCHEDULE, None, VACATION_MOMENT, vacation=vacation
    )
    assert same == VACATION_STATE
    assert changed_state(VACATION_STATE, None, None, VACATION_MOMENT) == VACATION_STATE

    end = datetime(2026, 11, 13, 15, 10, 5, tzinfo=ZONE)
    comfort = changed_state(MANUAL_STATE, REGULATION_COMFORT, 2450, end=end)
    again = changed_state(
        comfort,
        REGULATION_COMFORT,
        2450,
        EPOCH + timedelta(seconds=Message.parse(comfort).varint(1) or 0),
        end=end,
    )
    assert again == comfort


def test_changed_state_keeps_what_is_not_understood() -> None:
    """Fields that are not known survive, also inside a day or an end."""
    odd_day = encode_field(3, 1) + encode_field(9, b"day") + encode_field(1, 1970)
    odd_clock = encode_field(1, 0) + encode_field(7, 7)
    odd_end = (
        encode_field(4, b"end") + encode_field(2, odd_clock) + encode_field(1, odd_day)
    )
    state = (
        encode_field(1, 1000)
        + encode_field(2, 1)
        + encode_field(3, encode_field(2, odd_end) + encode_field(5, 5))
        + encode_field(5, encode_field(6, 6) + encode_field(3, odd_day))
        + encode_field(6, encode_field(1, odd_end) + encode_field(8, 8))
        + encode_field(11, b"state")
    )
    end = datetime(2026, 11, 13, 15, 10, 5, tzinfo=ZONE)
    vacation = Vacation(enabled=True, begin=date(2027, 1, 1), end=date(2027, 1, 2))
    # The numbers that were there are replaced where they are; the month and
    # the minute and second, which were missing, are put in their place.
    new_end = (
        encode_field(4, b"end")
        + encode_field(
            2,
            encode_field(1, 15)
            + encode_field(2, 10)
            + encode_field(3, 5)
            + encode_field(7, 7),
        )
        + encode_field(
            1,
            encode_field(2, 11)
            + encode_field(3, 13)
            + encode_field(9, b"day")
            + encode_field(1, 2026),
        )
    )

    comfort = changed_state(state, REGULATION_COMFORT, 2000, end=end, vacation=vacation)
    assert _other_fields(comfort, 1, 2, 3, 5) == _other_fields(state, 1, 2, 3, 5)
    assert _content(comfort, 3) == (
        encode_field(1, 4000) + encode_field(2, new_end) + encode_field(5, 5)
    )
    assert _content(comfort, 5) == (
        encode_field(1, 1)
        + encode_field(2, _day(2027, 1, 1))
        + encode_field(6, 6)
        + encode_field(
            3,
            encode_field(2, 1)
            + encode_field(3, 2)
            + encode_field(9, b"day")
            + encode_field(1, 2027),
        )
    )

    boost = changed_state(state, REGULATION_BOOST, end=end)
    assert _other_fields(boost, 1, 2, 6) == _other_fields(state, 1, 2, 6)
    assert _content(boost, 6) == encode_field(1, new_end) + encode_field(8, 8)


@freeze_time("2026-11-13 13:10:00")
def test_changed_state_defaults_to_now() -> None:
    """Without a moment the change is timed now."""
    changed = Message.parse(changed_state(MANUAL_STATE, REGULATION_SCHEDULE))
    assert changed.varint(1) == device_time()


@pytest.mark.parametrize(
    "state",
    [
        b"\x00",
        encode_field(3, b"\x00"),
        encode_field(3, encode_field(2, b"\x00")),
        encode_field(3, encode_field(2, encode_field(1, b"\x00"))),
        encode_field(3, encode_field(2, encode_field(2, b"\x00"))),
    ],
)
def test_changed_state_rejects_malformed_state(state: bytes) -> None:
    """A state with a part that is not a message cannot be changed."""
    end = datetime(2026, 11, 13, 15, 10, 5, tzinfo=ZONE)
    with pytest.raises(ValueError, match="protobuf"):
        changed_state(state, REGULATION_COMFORT, 2000, end=end)


def test_changed_state_rejects_a_negative_setpoint() -> None:
    """A setpoint below zero cannot go on the wire."""
    with pytest.raises(ValueError, match="negative"):
        changed_state(MANUAL_STATE, REGULATION_MANUAL, -100)


def test_command() -> None:
    """A command is a state that wants an answer."""
    assert command(101, b"state") == (
        encode_field(1, 101) + encode_field(4, 1) + encode_field(9, b"state")
    )
    # It has the shape of the command of OJ's gateway.
    assert command(3, MANUAL_STATE) == MANUAL_COMMAND


def test_vacation_mode_of_a_vacation_that_has_begun() -> None:
    """Observed: the thermostat goes to the vacation mode at once."""
    today = date(2026, 11, 13)
    starting = Vacation(enabled=True, begin=date(2026, 11, 13), end=date(2026, 11, 14))
    ending = Vacation(enabled=True, begin=date(2026, 11, 10), end=date(2026, 11, 13))

    assert vacation_mode(starting, REGULATION_SCHEDULE, today) == REGULATION_VACATION
    assert vacation_mode(ending, REGULATION_MANUAL, today) == REGULATION_VACATION
    assert vacation_mode(ending, None, today) == REGULATION_VACATION


def test_vacation_mode_of_a_vacation_in_the_future() -> None:
    """Observed: the mode stays as it is."""
    today = date(2026, 11, 13)
    later = Vacation(enabled=True, begin=date(2027, 1, 1), end=date(2027, 1, 3))
    earlier = Vacation(enabled=True, begin=date(2026, 11, 1), end=date(2026, 11, 12))

    assert vacation_mode(later, REGULATION_SCHEDULE, today) is None
    assert vacation_mode(earlier, REGULATION_MANUAL, today) is None
    assert vacation_mode(later, None, today) is None


def test_vacation_mode_leaves_the_vacation_mode() -> None:
    """Assumed: without a vacation that runs, the schedule takes over."""
    today = date(2026, 11, 13)
    disabled = Vacation(enabled=False, begin=date(2026, 11, 13), end=date(2026, 11, 14))
    moved = Vacation(enabled=True, begin=date(2027, 1, 1), end=date(2027, 1, 3))
    removed = Vacation(enabled=False)

    assert vacation_mode(disabled, REGULATION_VACATION, today) == REGULATION_SCHEDULE
    assert vacation_mode(moved, REGULATION_VACATION, today) == REGULATION_SCHEDULE
    assert vacation_mode(removed, REGULATION_VACATION, today) == REGULATION_SCHEDULE
    assert vacation_mode(removed, REGULATION_MANUAL, today) is None


def test_vacation_mode_without_both_days() -> None:
    """A vacation without its first or last day does not run."""
    today = date(2026, 11, 13)
    endless = Vacation(enabled=True, begin=date(2026, 11, 13))
    sudden = Vacation(enabled=True, end=date(2026, 11, 14))

    assert vacation_mode(endless, REGULATION_SCHEDULE, today) is None
    assert vacation_mode(sudden, REGULATION_SCHEDULE, today) is None
    assert vacation_mode(endless, REGULATION_VACATION, today) == REGULATION_SCHEDULE


def test_vacation_mode_compares_days() -> None:
    """Today may be a moment, as local_in() returns it, and so may the days."""
    # 23:30 UTC on the 12th is already the 13th at the thermostat.
    today = local_in(0, 3600, datetime(2026, 11, 12, 23, 30, tzinfo=UTC))
    starting = Vacation(enabled=True, begin=date(2026, 11, 13), end=date(2026, 11, 13))
    moments = Vacation(
        enabled=True,
        begin=datetime(2026, 11, 13, 12, tzinfo=ZONE),
        end=datetime(2026, 11, 13, 8, tzinfo=ZONE),
    )

    assert vacation_mode(starting, REGULATION_SCHEDULE, today) == REGULATION_VACATION
    assert vacation_mode(moments, REGULATION_SCHEDULE, today) == REGULATION_VACATION
    assert vacation_mode(starting, REGULATION_SCHEDULE, date(2026, 11, 12)) is None


def test_describe_logged_messages() -> None:
    """The summary shows what is decoded and the fields that are not."""
    assert describe(Message.parse(HEARTBEAT)) == (
        "seq=14 fields=1,4,6 room=19.73C floor=33.01C heating=1 6.5=1"
    )
    assert describe(Message.parse(UPLOAD)) == (
        "seq=13 fields=1,4,7,9 mode=1 (schedule) vacation=0 zone=3600 9.8=1 9.9=0"
    )
    assert describe(Message.parse(MANUAL_COMMAND)) == (
        "seq=3 fields=1,4,9 mode=3 (manual) comfort=23.00C "
        "comfort-end=1970-01-01T00:00:00 manual=23.00C frost-protection=5.00C "
        "9.8=1 9.9=0"
    )
    assert describe(Message.parse(command(101, VACATION_STATE))) == (
        "seq=101 fields=1,4,9 mode=1 (schedule) comfort=23.00C "
        "comfort-end=1970-01-01T00:00:00 manual=0.00C vacation=1 "
        "vacation-begin=2027-01-01T00:00:00 vacation-end=2027-01-02T00:00:00 "
        "vacation-setpoint=5.00C boost-end=1970-01-01T00:00:00 "
        "frost-protection=5.00C 9.8=1 9.9=0"
    )


def test_describe_answers() -> None:
    """An answer shows whether it accepts the message it answers."""
    assert describe(Message.parse(ACKNOWLEDGEMENT)) == "seq=0 fields=1,3 answer=1"
    assert describe(Message.parse(answer(13, MANUAL_MOMENT))) == (
        "seq=13 fields=1,3,12 answer=1"
    )
    assert describe(Message.parse(upload_request(MANUAL_MOMENT))) == (
        "seq=0 fields=1,4,10"
    )


def test_describe_never_shows_a_login_or_a_name() -> None:
    """A login is only named, and the name of a thermostat is left out."""
    assert describe(Message.parse(LOGIN)) == "login"
    assert "Bad" not in describe(Message.parse(UPLOAD))

    info = encode_field(7, encode_field(2, b"Bad") + encode_field(17, 350))
    assert describe(Message.parse(info)) == "seq=None fields=7 load=350"


def test_describe_unknown_modes_and_fields() -> None:
    """An unknown mode and fields that are not interpreted are shown as they are."""
    odd = (
        encode_field(2, 7)
        + encode_field(12, b"\xff")
        + encode_field(10, encode_field(1, 0) + encode_field(2, b"\x01\x02"))
        # A field that occurs twice is shown once.
        + encode_field(8, 1)
        + encode_field(8, 2)
    )
    assert describe(Message.parse(encode_field(9, odd))) == (
        "seq=None fields=9 mode=7 (unknown) 9.8=1 9.10={1: 0, 2: 0102} 9.12=ff"
    )
    # A state without a mode, and a heartbeat without values.
    assert describe(Message.parse(encode_field(9, b""))) == "seq=None fields=9"
    assert describe(Message.parse(encode_field(6, b""))) == "seq=None fields=6"


def test_describe_survives_malformed_parts() -> None:
    """Logging a message never raises, whatever the thermostat sent."""
    assert describe(Message.parse(encode_field(9, b"\x00"))) == (
        "seq=None fields=9 undecodable"
    )
    refused = encode_field(1, 101) + encode_field(3, 2) + encode_field(6, b"\x00")
    assert describe(Message.parse(refused)) == (
        "seq=101 fields=1,3,6 answer=2 undecodable"
    )


def test_time_zone_west_of_utc() -> None:
    """A negative time zone is read as the wire format writes it."""
    eastern = (1 << 64) - 18000
    info = encode_field(7, encode_field(3, eastern))
    assert decode_state(Message.parse(info)) == {"TimeZone": -18000}
    # The local time of that thermostat is five hours behind UTC.
    moment = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
    assert local_in(60, -18000, moment).isoformat() == "2026-11-14T08:00:00-05:00"
