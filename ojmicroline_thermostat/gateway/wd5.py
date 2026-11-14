"""Adapted from patricknitsch/ioBroker.schlueter-thermostat (MIT): WD5 messages.

Messages between a WD5-series thermostat (OWD5, MWD5) and its device gateway.
There is no published specification. The field numbers and their meaning are
as that project observed them on an OWD5 thermostat with firmware 1013W220;
see the LICENSE file in this package. The frames and the TLS connection are
those of the WG4 series; the fields are not.

Every message has a sequence number in field 1. An acknowledgement repeats
that number with field 3 set to 1. A message that wants an answer has field 4
set to 1.

From the thermostat:

    field 5          login: 5.1 the serial number, 5.2 the device type, 5.3
                     the firmware, 5.5 the clock {1 hour, 2 minute, 3 second}
    field 6          heartbeat, about once a minute: 6.1 the time, 6.2 the
                     room temperature, 6.3 the floor temperature, 6.4 whether
                     the floor is heating; 6.5 to 6.7 are not known
    field 7          device info: 7.2 the name, 7.3 the time zone in seconds,
                     7.16 the MAC address, 7.17 the load in W
    field 8          schedule: 8.2 the week, which has a day in every field 1
                     {1 the number of the day, 2 an event, once per event};
                     an event is {1 the clock, 2 the temperature, 3 whether
                     it is active, 4 whether it is on the next day}
    field 9          state, with only the fields that matter in its mode
    field 12         clock: 12.1 the time, 12.2 the time zone in seconds
    fields 1 and 3   acknowledgement of a command

To the thermostat:

    field 12         in the answer to a login, and to every other message
                     that wants an answer: 12.1 the server time
    field 10         after the answer to a login: 10.1 the server time and
                     10.2 = 1, the request for the full upload
    field 9          a command: the complete state the thermostat has to
                     take over

State (field 9): 9.1 the time of the change, 9.2 the regulation mode (the
codes of the cloud API: 1 schedule, 2 comfort, 3 manual, 4 vacation, 6 frost
protection, 8 boost, 9 eco), 9.3 comfort {1 setpoint, 2 end}, 9.4 manual
{1 setpoint}, 9.5 vacation {1 enabled, 2 first day, 3 last day, 4 setpoint},
9.6 boost {1 end} and 9.7 frost protection {1 setpoint}. What 9.8 and 9.9 mean
is not known.

Setpoints on the wire are in 1/200 °C, the measured temperatures and those of
the schedule in 1/100 °C. This library uses 1/100 °C everywhere. The times in
6.1, 9.1, 10.1 and 12.1 are seconds since 2000-01-01 UTC. A day is {1 year,
2 month, 3 day} and an end is {1 a day, 2 {1 hour, 2 minute, 3 second}}. Both
are in the local time of the thermostat, and 1970-01-01 means "not set".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import TYPE_CHECKING, Any

from ojmicroline_thermostat.const import (
    REGULATION_BOOST,
    REGULATION_COMFORT,
    REGULATION_ECO,
    REGULATION_FROST_PROTECTION,
    REGULATION_MANUAL,
    REGULATION_SCHEDULE,
    REGULATION_VACATION,
)

from .protobuf import (
    LENGTH_DELIMITED,
    Message,
    brief,
    encode_field,
    from_wire,
    set_field,
    to_wire,
)
from .wg4 import device_time

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

FIELD_SEQUENCE = 1
FIELD_ACKNOWLEDGED = 3
FIELD_WANTS_ANSWER = 4
FIELD_LOGIN = 5
FIELD_TELEMETRY = 6
FIELD_DEVICE_INFO = 7
FIELD_SCHEDULE = 8
FIELD_STATE = 9
FIELD_UPLOAD_REQUEST = 10
FIELD_SERVER_TIME = 12

# What a day or an end that is not set decodes to.
NOT_SET = "1970-01-01T00:00:00"

_LOGIN_SERIAL = 1
_LOGIN_DEVICE_TYPE = 2
_LOGIN_FIRMWARE = 3
_LOGIN_CLOCK = 5
_TELEMETRY_TIME = 1
_TELEMETRY_ROOM = 2
_TELEMETRY_FLOOR = 3
_TELEMETRY_HEATING = 4
_INFO_NAME = 2
_INFO_TIME_ZONE = 3
_INFO_LOAD = 17
_SCHEDULE_WEEK = 2
_WEEK_DAY = 1
_DAY_NUMBER = 1
_DAY_EVENT = 2
_EVENT_CLOCK = 1
_EVENT_TEMPERATURE = 2
_EVENT_ACTIVE = 3
_EVENT_NEXT_DAY = 4
_STATE_TIME = 1
_STATE_MODE = 2
_STATE_COMFORT = 3
_STATE_MANUAL = 4
_STATE_VACATION = 5
_STATE_BOOST = 6
_STATE_FROST_PROTECTION = 7
# Field 1 of comfort, manual and frost protection is their setpoint.
_SETPOINT = 1
_COMFORT_END = 2
_VACATION_ENABLED = 1
_VACATION_BEGIN = 2
_VACATION_END = 3
_VACATION_SETPOINT = 4
_BOOST_END = 1
_UPLOAD_TIME = 1
_UPLOAD_FULL = 2
_SERVER_TIME = 1
_END_DAY = 1
_END_CLOCK = 2

_EMPTY = Message(())
_UNSET_DAY = date(1970, 1, 1)
_SECONDS_PER_DAY = 86400

# The setpoints of a state that default_state() builds, by their name in the
# cloud API, and what each one is as long as it is not known. The defaults are
# those of the adapter this module is adapted from.
_DEFAULT_SETPOINTS = {
    "ComfortSetpoint": 2100,
    "ManualModeSetpoint": 0,
    "VacationTemperature": 500,
    "FrostProtectionTemperature": 500,
}

_MODE_NAMES = {
    REGULATION_SCHEDULE: "schedule",
    REGULATION_COMFORT: "comfort",
    REGULATION_MANUAL: "manual",
    REGULATION_VACATION: "vacation",
    REGULATION_FROST_PROTECTION: "frost protection",
    REGULATION_BOOST: "boost",
    REGULATION_ECO: "eco",
}

# What describe() shows of a decoded message, and under which name. The name
# of the thermostat is chosen by its owner and stays out of the log.
_LABELS = {
    "RoomTemperature": "room",
    "FloorTemperature": "floor",
    "Heating": "heating",
    "ComfortSetpoint": "comfort",
    "ComfortEndTime": "comfort-end",
    "ManualModeSetpoint": "manual",
    "VacationEnabled": "vacation",
    "VacationBeginDay": "vacation-begin",
    "VacationEndDay": "vacation-end",
    "VacationTemperature": "vacation-setpoint",
    "BoostEndTime": "boost-end",
    "FrostProtectionTemperature": "frost-protection",
    "TimeZone": "zone",
    "LoadWatts": "load",
}
_TEMPERATURES = frozenset(
    {
        "RoomTemperature",
        "FloorTemperature",
        "ComfortSetpoint",
        "ManualModeSetpoint",
        "VacationTemperature",
        "FrostProtectionTemperature",
    }
)
# The fields of a heartbeat and of a state that describe() interprets or, as
# their time, leaves out.
_TELEMETRY_SHOWN = frozenset(
    {_TELEMETRY_TIME, _TELEMETRY_ROOM, _TELEMETRY_FLOOR, _TELEMETRY_HEATING}
)
_STATE_SHOWN = frozenset(
    {
        _STATE_TIME,
        _STATE_MODE,
        _STATE_COMFORT,
        _STATE_MANUAL,
        _STATE_VACATION,
        _STATE_BOOST,
        _STATE_FROST_PROTECTION,
    }
)


@dataclass(frozen=True)
class Login:
    """What a thermostat tells about itself when it logs in.

    The serial number is SerialNumber in the cloud API and the firmware is
    SWversion. The clock is the local time of the thermostat. A part that the
    login does not hold, or that is not what it should be, is None.
    """

    serial: str
    device_type: int | None
    firmware: str | None
    clock: time | None


@dataclass(frozen=True)
class Vacation:
    """A vacation: whether it is enabled, and its first and last day.

    The days are in the local time of the thermostat. A day that is None is
    not set, which the thermostat stores as 1970-01-01.
    """

    enabled: bool
    begin: date | None = None
    end: date | None = None


def decode_login(message: Message) -> Login | None:
    """Return what a thermostat tells about itself in its login.

    Unlike the login of the WG4 series, this one holds no email address.

    Args:
    ----
        message: The first message of a connection.

    Returns:
    -------
        The login, or None if the message is not one: it has no field 5 that
        holds a serial number of printable characters.

    Raises:
    ------
        ValueError: The login is malformed.

    """
    login = message.message(FIELD_LOGIN)
    if login is None:
        return None
    serial = _text(login.data(_LOGIN_SERIAL))
    if serial is None:
        return None
    return Login(
        serial=serial,
        device_type=login.varint(_LOGIN_DEVICE_TYPE),
        firmware=_text(login.data(_LOGIN_FIRMWARE)),
        clock=_clock(login.message(_LOGIN_CLOCK)),
    )


def is_answer(message: Message) -> bool:
    """Tell whether a message acknowledges a message that was sent."""
    return FIELD_ACKNOWLEDGED in message and FIELD_WANTS_ANSWER not in message


def wants_answer(message: Message) -> bool:
    """Tell whether a message wants an answer, see answer()."""
    return message.varint(FIELD_WANTS_ANSWER) == 1 and FIELD_ACKNOWLEDGED not in message


def answer(sequence: int, moment: datetime | None = None) -> bytes:
    """Build the answer to a message that wants one, as OJ's gateway sends it.

    Args:
    ----
        sequence: The sequence number of the message.
        moment: The server time as a timezone-aware moment; now when omitted.

    Returns:
    -------
        The encoded message.

    """
    return (
        encode_field(FIELD_SEQUENCE, sequence)
        + encode_field(FIELD_ACKNOWLEDGED, 1)
        + _server_time(moment)
    )


def login_answer(sequence: int, moment: datetime | None = None) -> bytes:
    """Build the answer to a login, which also tells the thermostat the time.

    Args:
    ----
        sequence: The sequence number of the login.
        moment: The server time as a timezone-aware moment; now when omitted.

    Returns:
    -------
        The encoded message.

    """
    return (
        encode_field(FIELD_SEQUENCE, sequence)
        + encode_field(FIELD_ACKNOWLEDGED, 1)
        + encode_field(FIELD_WANTS_ANSWER, 1)
        + _server_time(moment)
    )


def upload_request(moment: datetime | None = None) -> bytes:
    """Build the request for the full upload, sent after the answer to a login.

    The thermostat acknowledges it, and then uploads its device info, its
    schedule and its state.

    Args:
    ----
        moment: The server time as a timezone-aware moment; now when omitted.

    Returns:
    -------
        The encoded message. Its sequence number is 0.

    """
    request = encode_field(_UPLOAD_TIME, device_time(moment)) + encode_field(
        _UPLOAD_FULL, 1
    )
    return (
        encode_field(FIELD_SEQUENCE, 0)
        + encode_field(FIELD_WANTS_ANSWER, 1)
        + encode_field(FIELD_UPLOAD_REQUEST, request)
    )


def decode_telemetry(message: Message) -> dict[str, Any]:
    """Translate the heartbeat of a message to cloud API fields.

    Args:
    ----
        message: A message from the thermostat.

    Returns:
    -------
        The room and floor temperature in 1/100 °C and whether the floor is
        heating, as far as the message reports them.

    Raises:
    ------
        ValueError: The heartbeat is malformed.

    """
    telemetry = _nested(message, FIELD_TELEMETRY)
    return _reported(
        {
            "RoomTemperature": telemetry.varint(_TELEMETRY_ROOM),
            "FloorTemperature": telemetry.varint(_TELEMETRY_FLOOR),
            "Heating": _flag(telemetry, _TELEMETRY_HEATING),
        }
    )


def decode_state(message: Message) -> dict[str, Any]:
    """Translate the state and device info of a message to cloud API fields.

    The result uses the names and units of the thermostat JSON of the cloud
    API, so it can be laid over that JSON. Only LoadWatts is not in that JSON.
    A thermostat reports the fields that matter in its mode, so most messages
    give some of these fields.

    A day or an end that is not set is NOT_SET, as the thermostat sends it.
    One that is no date at all is left out.

    Args:
    ----
        message: A message from the thermostat, or a command sent to it.

    Returns:
    -------
        The fields the message reports; empty if it has neither state nor
        device info.

    Raises:
    ------
        ValueError: The state or the device info is malformed.

    """
    state = _nested(message, FIELD_STATE)
    comfort = _nested(state, _STATE_COMFORT)
    vacation = _nested(state, _STATE_VACATION)
    boost = _nested(state, _STATE_BOOST)
    info = _nested(message, FIELD_DEVICE_INFO)
    name = info.data(_INFO_NAME)
    return _reported(
        {
            "RegulationMode": state.varint(_STATE_MODE),
            "ComfortSetpoint": _setpoint(comfort, _SETPOINT),
            "ComfortEndTime": decode_date_time(comfort.message(_COMFORT_END)),
            "ManualModeSetpoint": _setpoint(_nested(state, _STATE_MANUAL), _SETPOINT),
            "VacationEnabled": _flag(vacation, _VACATION_ENABLED),
            "VacationBeginDay": decode_date(vacation.message(_VACATION_BEGIN)),
            "VacationEndDay": decode_date(vacation.message(_VACATION_END)),
            "VacationTemperature": _setpoint(vacation, _VACATION_SETPOINT),
            "BoostEndTime": decode_date_time(boost.message(_BOOST_END)),
            "FrostProtectionTemperature": _setpoint(
                _nested(state, _STATE_FROST_PROTECTION), _SETPOINT
            ),
            "ThermostatName": None if name is None else name.decode(errors="replace"),
            "TimeZone": _signed(info.varint(_INFO_TIME_ZONE)),
            "LoadWatts": info.varint(_INFO_LOAD),
        }
    )


def decode_schedule(message: Message) -> dict[str, Any] | None:
    """Translate the schedule of a message to the Schedule of the cloud API.

    The result has the days and events of that Schedule, in the order the
    thermostat sent them, without ScheduleType and ModifiedDueToVerification:
    the thermostat sends neither. A part of a day or an event that it did not
    send is left out as well.

    Args:
    ----
        message: A message from the thermostat.

    Returns:
    -------
        The schedule, or None if the message has none.

    Raises:
    ------
        ValueError: The schedule is malformed.

    """
    week = _nested(message, FIELD_SCHEDULE).message(_SCHEDULE_WEEK)
    if week is None:
        return None
    return {"Days": [_decode_day(day) for day in _repeated(week, _WEEK_DAY)]}


def decode_date_time(fields: Message | None) -> str | None:
    """Return an end, {1 a day, 2 a time}, the way the cloud API writes it.

    Args:
    ----
        fields: The end, or None if the thermostat sent none.

    Returns:
    -------
        The local time of the thermostat as YYYY-MM-DDTHH:MM:SS, which is
        NOT_SET for an end that is not set. None without an end, or if it is
        not a date and a time.

    Raises:
    ------
        ValueError: The end is malformed.

    """
    if fields is None:
        return None
    day = _day(fields.message(_END_DAY))
    clock = _clock(fields.message(_END_CLOCK))
    if day is None or clock is None:
        return None
    return f"{day.isoformat()}T{clock.isoformat()}"


def decode_date(fields: Message | None) -> str | None:
    """Return a day, {1 year, 2 month, 3 day}, the way the cloud API writes it.

    Args:
    ----
        fields: The day, or None if the thermostat sent none.

    Returns:
    -------
        The day as YYYY-MM-DDT00:00:00, which is NOT_SET for a day that is not
        set. None without a day, or if it is not a date.

    """
    day = _day(fields)
    return None if day is None else f"{day.isoformat()}T00:00:00"


def encode_date(day: date | None) -> bytes:
    """Encode a day as {1 year, 2 month, 3 day}; None is a day that is not set."""
    return _with_day(b"", day)


def encode_date_time(local: datetime | None) -> bytes:
    """Encode an end as {1 a day, 2 a time}.

    Args:
    ----
        local: The end in the local time of the thermostat, see local_in().
            Its date and time are written as they are, whatever its time zone.
            None is an end that is not set.

    Returns:
    -------
        The encoded end.

    """
    return _with_end(b"", local)


def local_in(minutes: int, time_zone: int, moment: datetime | None = None) -> datetime:
    """Return the local time of a thermostat, some minutes from a moment.

    Args:
    ----
        minutes: The number of minutes after the moment.
        time_zone: The offset of the thermostat to UTC in seconds, which is
            TimeZone in what decode_state() returns.
        moment: A timezone-aware moment; now when omitted.

    Returns:
    -------
        That time in the time zone of the thermostat, for changed_state() and
        vacation_mode().

    Raises:
    ------
        ValueError: The time zone is a day or more away from UTC.

    """
    if abs(time_zone) >= _SECONDS_PER_DAY:
        msg = f"A time zone of {time_zone} seconds is a day or more away from UTC"
        raise ValueError(msg)
    if moment is None:
        moment = datetime.now(tz=UTC)
    zone = timezone(timedelta(seconds=time_zone))
    return (moment + timedelta(minutes=minutes)).astimezone(zone)


def default_state(
    fields: Mapping[str, Any] | None = None, moment: datetime | None = None
) -> bytes:
    """Build a complete state as OJ's gateway sends it, from what is known.

    A thermostat reports only the fields that matter in its mode, while a
    command holds a complete state. This state is the base of a command,
    see changed_state(), as long as no command of OJ's gateway was seen that
    can be the base. It is in the schedule mode, has no vacation, and no end of
    a comfort hold or of a boost.

    A thermostat that takes this state over loses what it has stored in the
    fields that are not known here, so pass every setpoint that is known.

    Args:
    ----
        fields: What is known of the thermostat, by the names of the cloud
            API: decode_state() results, or the thermostat JSON of that API.
            Of these, ComfortSetpoint, ManualModeSetpoint, VacationTemperature
            and FrostProtectionTemperature are used. A setpoint that is not
            known becomes 21 °C for comfort, 0 °C for manual and 5 °C for
            the other two.
        moment: The time of the change as a timezone-aware moment; now when
            omitted.

    Returns:
    -------
        The encoded state.

    """
    known = {} if fields is None else fields
    wire = {
        name: to_wire(default if known.get(name) is None else known[name])
        for name, default in _DEFAULT_SETPOINTS.items()
    }
    not_set = encode_date_time(None)
    comfort = encode_field(_SETPOINT, wire["ComfortSetpoint"]) + encode_field(
        _COMFORT_END, not_set
    )
    vacation = (
        encode_field(_VACATION_ENABLED, 0)
        + encode_field(_VACATION_BEGIN, encode_date(None))
        + encode_field(_VACATION_END, encode_date(None))
        + encode_field(_VACATION_SETPOINT, wire["VacationTemperature"])
    )
    return (
        encode_field(_STATE_TIME, device_time(moment))
        + encode_field(_STATE_MODE, REGULATION_SCHEDULE)
        + encode_field(_STATE_COMFORT, comfort)
        + encode_field(
            _STATE_MANUAL, encode_field(_SETPOINT, wire["ManualModeSetpoint"])
        )
        + encode_field(_STATE_VACATION, vacation)
        + encode_field(_STATE_BOOST, encode_field(_BOOST_END, not_set))
        + encode_field(
            _STATE_FROST_PROTECTION,
            encode_field(_SETPOINT, wire["FrostProtectionTemperature"]),
        )
        # OJ's gateway sends 9.8 = 1 and 9.9 = 0; what they mean is not known.
        + encode_field(8, 1)
        + encode_field(9, 0)
    )


# Each argument after the state is a separate part of the change.
def changed_state(  # noqa: PLR0913 # pylint: disable=too-many-arguments
    state: bytes,
    mode: int | None = None,
    temperature: int | None = None,
    moment: datetime | None = None,
    *,
    end: datetime | None = None,
    vacation: Vacation | None = None,
) -> bytes:
    """Return a complete state with another mode, setpoint, end or vacation.

    The result is the state with a new time. Only the fields named here are
    changed, each in its place: everything else, down to the parts of a day
    or an end that are not understood, stays as it was sent.

    Args:
    ----
        state: A complete state (field 9): the last one OJ's gateway or this
            gateway sent the thermostat, or default_state().
        mode: The regulation mode to switch to; None keeps the mode.
        temperature: The setpoint in 1/100 °C, if the mode given here is the
            comfort or the manual mode; it goes into the field of that mode.
            None keeps the setpoint. With another mode, or without one, it is
            ignored.
        moment: The time of the change as a timezone-aware moment; now when
            omitted.
        end: The end of the hold or the boost, if the mode given here is the
            comfort or the boost mode, in the local time of the thermostat,
            see local_in(). None keeps the end the state has, which has
            passed unless the thermostat is in that mode. With another mode,
            or without one, it is ignored.
        vacation: The vacation to store. Its setpoint stays as it is, as in
            the commands of OJ's gateway. See vacation_mode() for the mode
            that goes with it.

    Returns:
    -------
        The encoded state.

    Raises:
    ------
        ValueError: The state is malformed, or the temperature is negative.

    """
    changed = set_field(state, _STATE_TIME, device_time(moment))
    if mode is not None:
        changed = set_field(changed, _STATE_MODE, mode)
    if vacation is not None:
        stored = _content(state, _STATE_VACATION)
        begin = _with_day(_content(stored, _VACATION_BEGIN), vacation.begin)
        last = _with_day(_content(stored, _VACATION_END), vacation.end)
        stored = set_field(stored, _VACATION_ENABLED, int(vacation.enabled))
        stored = set_field(stored, _VACATION_BEGIN, begin)
        stored = set_field(stored, _VACATION_END, last)
        changed = set_field(changed, _STATE_VACATION, stored)
    if mode == REGULATION_MANUAL and temperature is not None:
        manual = _content(state, _STATE_MANUAL)
        manual = set_field(manual, _SETPOINT, to_wire(temperature))
        changed = set_field(changed, _STATE_MANUAL, manual)
    if mode == REGULATION_COMFORT:
        comfort = _content(state, _STATE_COMFORT)
        if temperature is not None:
            comfort = set_field(comfort, _SETPOINT, to_wire(temperature))
        if end is not None:
            until = _with_end(_content(comfort, _COMFORT_END), end)
            comfort = set_field(comfort, _COMFORT_END, until)
        if temperature is not None or end is not None:
            changed = set_field(changed, _STATE_COMFORT, comfort)
    if mode == REGULATION_BOOST and end is not None:
        boost = _content(state, _STATE_BOOST)
        until = _with_end(_content(boost, _BOOST_END), end)
        changed = set_field(changed, _STATE_BOOST, set_field(boost, _BOOST_END, until))
    return changed


def command(sequence: int, state: bytes) -> bytes:
    """Build the message that makes the thermostat take over a state.

    Args:
    ----
        sequence: The sequence number, which the thermostat acknowledges.
        state: The complete state (field 9) to take over, see changed_state().

    Returns:
    -------
        The encoded message.

    """
    return (
        encode_field(FIELD_SEQUENCE, sequence)
        + encode_field(FIELD_WANTS_ANSWER, 1)
        + encode_field(FIELD_STATE, state)
    )


def vacation_mode(vacation: Vacation, mode: int | None, today: date) -> int | None:
    """Return the regulation mode that goes with a change of the vacation.

    OJ's gateway puts a thermostat in the vacation mode at once if the
    vacation has begun. That a thermostat in the vacation mode returns to the
    schedule when its vacation is disabled or moved away is assumed.

    Args:
    ----
        vacation: The vacation to store.
        mode: The regulation mode the thermostat is in, if that is known.
        today: The day it is at the thermostat, see local_in().

    Returns:
    -------
        The mode to pass to changed_state(); None keeps the mode.

    """
    if (
        vacation.enabled
        and vacation.begin is not None
        and vacation.end is not None
        and _ordinal(vacation.begin) <= _ordinal(today) <= _ordinal(vacation.end)
    ):
        return REGULATION_VACATION
    return REGULATION_SCHEDULE if mode == REGULATION_VACATION else None


def describe(message: Message) -> str:
    """Summarise a message for debug logs.

    A login is named but not shown, and neither is the name of the thermostat.
    """
    if FIELD_LOGIN in message:
        return "login"
    parts = [
        f"seq={message.varint(FIELD_SEQUENCE)}",
        "fields=" + ",".join(str(number) for number in sorted(message.numbers)),
    ]
    acknowledged = message.varint(FIELD_ACKNOWLEDGED)
    if acknowledged is not None:
        parts.append(f"answer={acknowledged}")
    try:
        fields = decode_telemetry(message) | decode_state(message)
        unknown = [
            *_uninterpreted(message, FIELD_TELEMETRY, _TELEMETRY_SHOWN),
            *_uninterpreted(message, FIELD_STATE, _STATE_SHOWN),
        ]
    except ValueError:
        return " ".join([*parts, "undecodable"])
    mode = fields.get("RegulationMode")
    if mode is not None:
        parts.append(f"mode={mode} ({_MODE_NAMES.get(mode, 'unknown')})")
    parts += [
        f"{label}={_shown(name, fields[name])}"
        for name, label in _LABELS.items()
        if name in fields
    ]
    return " ".join(parts + unknown)


def _server_time(moment: datetime | None) -> bytes:
    """Encode the field that tells the thermostat the time."""
    return encode_field(
        FIELD_SERVER_TIME, encode_field(_SERVER_TIME, device_time(moment))
    )


def _text(data: bytes | None) -> str | None:
    """Return the text of a field, if it is printable ASCII and not empty."""
    if not data or not all(0x20 <= byte <= 0x7E for byte in data):
        return None
    return data.decode("ascii")


def _nested(fields: Message, number: int) -> Message:
    """Return a nested message, which is empty if the field is not there."""
    nested = fields.message(number)
    return _EMPTY if nested is None else nested


def _repeated(fields: Message, number: int) -> Iterator[Message]:
    """Parse every occurrence of a field that holds a nested message."""
    for field in fields.fields:
        if (
            field.number == number
            and field.wire_type == LENGTH_DELIMITED
            and isinstance(field.value, bytes)
        ):
            yield Message.parse(field.value)


def _content(data: bytes, number: int) -> bytes:
    """Return the content of a nested message, empty if the field is not there."""
    return Message.parse(data).data(number) or b""


def _reported(fields: dict[str, Any]) -> dict[str, Any]:
    """Leave out what a message does not report."""
    return {name: value for name, value in fields.items() if value is not None}


def _signed(value: int | None) -> int | None:
    """Read a number that may be negative, such as a time zone west of UTC.

    The wire format writes a negative number as its 64-bit two's complement.
    No thermostat west of UTC was looked at, so this is how the format works
    and not something that was seen.
    """
    if value is None or value < 1 << 63:
        return value
    return value - (1 << 64)


def _flag(fields: Message, number: int) -> bool | None:
    """Return whether a field is 1, or None if it is not there."""
    value = fields.varint(number)
    return None if value is None else value == 1


def _setpoint(fields: Message, number: int) -> int | None:
    """Return a setpoint in 1/100 °C, or None if it is not there."""
    value = fields.varint(number)
    return None if value is None else from_wire(value)


def _numbers(fields: Message) -> tuple[int, int, int]:
    """Return fields 1 to 3; one that is missing counts as 0, as in protobuf."""
    return (fields.varint(1) or 0, fields.varint(2) or 0, fields.varint(3) or 0)


def _day(fields: Message | None) -> date | None:
    """Return the day in {1 year, 2 month, 3 day}; None if it is not a date."""
    if fields is None:
        return None
    try:
        return date(*_numbers(fields))
    except (ValueError, OverflowError):
        return None


def _clock(fields: Message | None) -> time | None:
    """Return the time in {1 hour, 2 minute, 3 second}; None if it is not one."""
    if fields is None:
        return None
    try:
        return time(*_numbers(fields))
    except (ValueError, OverflowError):
        return None


def _ordinal(day: date) -> tuple[int, int, int]:
    """Return what orders days, also when one of them comes with a time."""
    return (day.year, day.month, day.day)


def _with_numbers(data: bytes, *numbers: int) -> bytes:
    """Set fields 1, 2 and so on, and keep whatever else the message holds."""
    for number, value in enumerate(numbers, start=1):
        data = set_field(data, number, value)
    return data


def _with_day(data: bytes, day: date | None) -> bytes:
    """Set the day in {1 year, 2 month, 3 day}; None is a day that is not set."""
    if day is None:
        day = _UNSET_DAY
    return _with_numbers(data, day.year, day.month, day.day)


def _with_end(data: bytes, local: datetime | None) -> bytes:
    """Set the day and the time in {1 a day, 2 a time}; None is not set."""
    clock = (0, 0, 0) if local is None else (local.hour, local.minute, local.second)
    data = set_field(data, _END_DAY, _with_day(_content(data, _END_DAY), local))
    return set_field(
        data, _END_CLOCK, _with_numbers(_content(data, _END_CLOCK), *clock)
    )


def _decode_day(day: Message) -> dict[str, Any]:
    """Translate a day of the schedule."""
    return _reported(
        {
            "WeekDayGrpNo": day.varint(_DAY_NUMBER),
            "Events": [_decode_event(event) for event in _repeated(day, _DAY_EVENT)],
        }
    )


def _decode_event(event: Message) -> dict[str, Any]:
    """Translate an event of the schedule."""
    clock = _clock(event.message(_EVENT_CLOCK))
    return _reported(
        {
            "Clock": None if clock is None else clock.isoformat(),
            "Temperature": event.varint(_EVENT_TEMPERATURE),
            "Active": _flag(event, _EVENT_ACTIVE),
            "EventIsOnNextDay": _flag(event, _EVENT_NEXT_DAY),
        }
    )


def _shown(name: str, value: Any) -> str:
    """Show a decoded value; a temperature in °C."""
    if name in _TEMPERATURES:
        return f"{value / 100:.2f}C"
    if isinstance(value, bool):
        return str(int(value))
    return str(value)


def _uninterpreted(
    message: Message, number: int, interpreted: frozenset[int]
) -> list[str]:
    """Show the fields of a nested message that are not interpreted."""
    parts = []
    shown = set(interpreted)
    nested = _nested(message, number)
    for field in sorted(nested.fields, key=lambda field: field.number):
        if field.number not in shown:
            shown.add(field.number)
            parts.append(f"{number}.{field.number}={brief(field.value)}")
    return parts
