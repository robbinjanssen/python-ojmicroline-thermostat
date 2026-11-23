"""Messages between a WG4-series thermostat and its device gateway.

There is no published specification. The field numbers and their meaning are
as observed by peskyAdmin/schluter-local (MIT, see the LICENSE file in this
package), from which this module is adapted.

Every message has a sequence number in field 1. An acknowledgement repeats
that number with field 3 set to 1. Field 5 is 1 on most messages in both
directions; what it means is not known.

From the thermostat:

    field 6          login: 6.1 the account's email address, 6.2 the serial
                     number
    field 2 = 6      upload of the schedule and settings
    field 2 = 4      full upload after login: 7 device info, 8 schedule,
                     9 state
    field 9 alone    someone changed the thermostat at the wall
    field 7 alone    the device info changed
    field 15         heartbeat, about once a minute: 15.1 the floor
                     temperature, 15.2 whether the floor is heating
    fields 1 and 3   acknowledgement of a command

To the thermostat:

    field 12         in the answer to a login: 12.1 the server time
    field 10         configuration, the answer to the full upload
    field 18         support information, the answer to the schedule upload
    fields 8 and 9   a command: the schedule and the state the thermostat
                     has to take over

State (field 9): 9.1 the time of the change, 9.2 the regulation mode, 9.4 the
temporary hold (9.4.1 its setpoint, 9.4.2 its end time) and 9.5 the permanent
hold (9.5.1 its setpoint). The thermostat keeps both setpoints all the time
and uses the one of the current mode. Device info 7.2 is the room name, and
7.9 the setpoint the thermostat is aiming for right now, in any mode.

Schedule (field 8), as reported in issue 480 of the Home Assistant
integration: 8.1 the time of the change and 8.2 the week, which has a day in
every field 1. A day is 1 its number (1 Monday to 7 Sunday) and 2 an event,
once per event. An event is 1 the clock (1 hour, 2 minute, 3 second), 2 the
temperature and 3 whether it is active. The thermostat that was looked at has
six events (slots) a day, which keep their place: an event that is switched
off keeps its time and temperature.

Setpoints on the wire are in 1/200 °C, the floor temperature and the
temperatures of the schedule in 1/100 °C. This library uses 1/100 °C
everywhere. Times are seconds since 2000-01-01 UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import combinations
from typing import TYPE_CHECKING, Any, NamedTuple

from ojmicroline_thermostat.const import (
    REGULATION_COMFORT,
    REGULATION_MANUAL,
    REGULATION_SCHEDULE,
    REGULATION_VACATION,
)

from .protobuf import (
    LENGTH_DELIMITED,
    SETPOINT_SCALE,
    Message,
    brief,
    encode_field,
    from_wire,
    set_field,
    set_repeated,
    to_wire,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

FIELD_SEQUENCE = 1
FIELD_KIND = 2
FIELD_ACKNOWLEDGED = 3
FIELD_LOGIN = 6
FIELD_DEVICE_INFO = 7
FIELD_SCHEDULE = 8
FIELD_STATE = 9
FIELD_CONFIGURATION = 10
FIELD_SERVER_TIME = 12
FIELD_TELEMETRY = 15
FIELD_SUPPORT = 18

# Values of FIELD_KIND in the uploads that follow a login.
KIND_STATE_UPLOAD = 4
KIND_SCHEDULE_UPLOAD = 6

EPOCH = datetime(2000, 1, 1, tzinfo=UTC)

_STATE_TIME = 1
_STATE_MODE = 2
_TEMPORARY_HOLD = 4
_PERMANENT_HOLD = 5
_HOLD_SETPOINT = 1
_HOLD_END_TIME = 2
_LOGIN_SERIAL = 2
_INFO_ROOM = 2
_INFO_TARGET = 9
_TELEMETRY_FLOOR = 1
_TELEMETRY_HEATING = 2
# Field 5 is 1 on most messages in both directions; its meaning is not known.
_FIELD_5 = 5

# The schedule (field 8), its week, a day of the week, and an event of a day.
_SCHEDULE_TIME = 1
_SCHEDULE_WEEK = 2
_WEEK_DAY = 1
_DAY_NUMBER = 1
_DAY_EVENT = 2
_EVENT_CLOCK = 1
_EVENT_TEMPERATURE = 2
_EVENT_ACTIVE = 3
# The fields of a clock: hour, minute and second.
_CLOCK_FIELDS = (1, 2, 3)

# The events a day of the schedule has, active or not.
SCHEDULE_SLOTS = 6
# The times of the events, in seconds since midnight, are quarters of an hour.
SCHEDULE_STEP = 900
_DAY = 86400

# The field of the state that holds the setpoint of a mode, and the name of
# that setpoint in the cloud API.
_HOLD_FIELDS = {
    REGULATION_COMFORT: _TEMPORARY_HOLD,
    REGULATION_MANUAL: _PERMANENT_HOLD,
}
_HOLD_NAMES = {
    _TEMPORARY_HOLD: "ComfortTemperature",
    _PERMANENT_HOLD: "ManualTemperature",
}
_MODE_NAMES = {
    REGULATION_SCHEDULE: "schedule",
    REGULATION_COMFORT: "temporary hold",
    REGULATION_MANUAL: "permanent hold",
    REGULATION_VACATION: "vacation",
}


def device_time(moment: datetime | None = None) -> int:
    """Return a moment the way the thermostat counts time.

    Args:
    ----
        moment: A timezone-aware moment; now when omitted.

    Returns:
    -------
        The seconds since 2000-01-01 UTC.

    """
    if moment is None:
        moment = datetime.now(tz=UTC)
    return int((moment - EPOCH).total_seconds())


def acknowledgement(sequence: int) -> bytes:
    """Build the message that acknowledges a message of the thermostat."""
    return encode_field(FIELD_SEQUENCE, sequence) + encode_field(FIELD_ACKNOWLEDGED, 1)


def login_acknowledgement(sequence: int, moment: datetime | None = None) -> bytes:
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
        acknowledgement(sequence)
        # OJ's gateway sets field 4 to 1 in this answer.
        + encode_field(4, 1)
        + encode_field(FIELD_SERVER_TIME, encode_field(1, device_time(moment)))
    )


def login_serial(message: Message) -> str | None:
    """Return the serial number from a login, or None if there is none.

    Raises
    ------
        ValueError: The login is malformed.

    """
    login = message.message(FIELD_LOGIN)
    if login is None:
        return None
    serial = login.data(_LOGIN_SERIAL)
    if not serial:
        return None
    return serial.decode(errors="replace")


def decode_state(message: Message) -> dict[str, Any]:
    """Translate the state and device info of a message to cloud API fields.

    The result uses the names and units of the thermostat JSON of the cloud
    API, so it can be laid over that JSON.

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
    fields: dict[str, Any] = {}
    state = message.message(FIELD_STATE)
    if state is not None:
        mode = state.varint(_STATE_MODE)
        if mode is not None:
            fields["RegulationMode"] = mode
        setpoints = _hold_setpoints(state)
        for hold, setpoint in setpoints.items():
            fields[_HOLD_NAMES[hold]] = from_wire(setpoint)
        active = None if mode is None else _HOLD_FIELDS.get(mode)
        if active in setpoints:
            fields["SetPointTemp"] = from_wire(setpoints[active])
    info = message.message(FIELD_DEVICE_INFO)
    if info is not None:
        room = info.data(_INFO_ROOM)
        if room is not None:
            fields["Room"] = room.decode(errors="replace")
        # The only place where the current setpoint of the schedule shows up.
        target = info.varint(_INFO_TARGET)
        if target is not None:
            fields["SetPointTemp"] = from_wire(target)
    return fields


def decode_telemetry(message: Message) -> dict[str, Any]:
    """Translate the heartbeat of a message to cloud API fields.

    Args:
    ----
        message: A message from the thermostat.

    Returns:
    -------
        The floor temperature and whether the floor is heating, as far as the
        message reports them.

    Raises:
    ------
        ValueError: The heartbeat is malformed.

    """
    fields: dict[str, Any] = {}
    telemetry = message.message(FIELD_TELEMETRY)
    if telemetry is None:
        return fields
    temperature = telemetry.varint(_TELEMETRY_FLOOR)
    if temperature is not None:
        fields["Temperature"] = temperature
    heating = telemetry.varint(_TELEMETRY_HEATING)
    if heating is not None:
        fields["Heating"] = bool(heating)
    return fields


def changed_state(
    state: bytes,
    mode: int,
    temperature: int | None = None,
    moment: datetime | None = None,
) -> bytes:
    """Return the state of a thermostat with another mode and setpoint.

    The result is the thermostat's own state with a new time and the new mode.
    The setpoint of a hold goes into the field of that hold; written to the
    other hold's field, the thermostat ignores it. Everything else, such as
    the other hold's setpoint and the end time of the temporary hold, stays as
    the thermostat sent it.

    Args:
    ----
        state: The state (field 9) the thermostat sent last.
        mode: The regulation mode to switch to.
        temperature: The setpoint in 1/100 °C for a temporary or permanent
            hold. Other modes have no setpoint in the state and ignore it.
        moment: The time of the change as a timezone-aware moment; now when
            omitted.

    Returns:
    -------
        The encoded state.

    Raises:
    ------
        ValueError: The state is malformed.

    """
    changed = set_field(state, _STATE_TIME, device_time(moment))
    changed = set_field(changed, _STATE_MODE, mode)
    hold = _HOLD_FIELDS.get(mode)
    if hold is not None and temperature is not None:
        current = Message.parse(state).data(hold) or b""
        changed = set_field(
            changed, hold, set_field(current, _HOLD_SETPOINT, to_wire(temperature))
        )
    return changed


def command(sequence: int, state: bytes, schedule: bytes | None = None) -> bytes:
    """Build the message that makes the thermostat take over a state.

    Args:
    ----
        sequence: The sequence number, which the thermostat acknowledges.
        state: The state (field 9) to take over, see changed_state().
        schedule: The schedule (field 8) the thermostat sent last, if any.

    Returns:
    -------
        The encoded message.

    """
    message = encode_field(FIELD_SEQUENCE, sequence) + encode_field(_FIELD_5, 1)
    if schedule:
        message += encode_field(FIELD_SCHEDULE, schedule)
    return message + encode_field(FIELD_STATE, state)


class _Slot(NamedTuple):
    """An event of a day of the schedule, at its place in the day."""

    seconds: int
    temperature: int
    active: bool


def decode_schedule(schedule: bytes) -> list[dict[str, Any]]:
    """Translate a schedule to the Schedules field of the cloud API.

    Args:
    ----
        schedule: The schedule (field 8) of a thermostat.

    Returns:
    -------
        Every day, Monday (WeekDayGrpNo 1) first, each with its six events:
        ScheduleType the place of the event in the day, Clock its time as
        HH:MM:SS, TempFloor its temperature in 1/100 °C and Active whether it
        is switched on.

    Raises:
    ------
        ValueError: The schedule is malformed, or does not have seven days
            of six events each.

    """
    days = _read_days(_week(schedule))
    return [
        {
            "WeekDayGrpNo": number,
            "Events": [
                {
                    "ScheduleType": place,
                    "Clock": _clock(slot.seconds),
                    "TempFloor": slot.temperature,
                    "Active": slot.active,
                }
                for place, slot in enumerate(days[number])
            ],
        }
        for number in sorted(days)
    ]


def decode_schedule_fields(message: Message) -> dict[str, Any]:
    """Translate the schedule of a message to the Schedules field of the cloud API.

    A schedule that is not understood is left out rather than refused: the
    gateway passes it back to the thermostat as it is, and the rest of the
    message still counts.

    Returns
    -------
        The Schedules field, see decode_schedule(); empty if the message has
        no schedule, or one that is not understood.

    """
    schedule = message.data(FIELD_SCHEDULE)
    if schedule is None:
        return {}
    try:
        return {"Schedules": decode_schedule(schedule)}
    except ValueError:
        return {}


def check_days(days: Iterable[int]) -> frozenset[int]:
    """Check the days of the week to change in a schedule.

    Args:
    ----
        days: The days, 0 for Monday to 6 for Sunday.

    Returns:
    -------
        The days, each once.

    Raises:
    ------
        ValueError: There is no day, or one that is not a day of the week.

    """
    chosen = frozenset(days)
    if not chosen or not chosen <= set(range(7)):
        msg = "Choose one or more days, 0 for Monday to 6 for Sunday."
        raise ValueError(msg)
    return chosen


def check_events(
    events: Sequence[tuple[int, int]],
    minimum: int | None = None,
    maximum: int | None = None,
) -> None:
    """Check the events of a day of the schedule.

    Args:
    ----
        events: Each event as its time in seconds since midnight and its
            temperature in 1/100 °C.
        minimum: The lowest temperature the thermostat allows, if known.
        maximum: The highest temperature the thermostat allows, if known.

    Raises:
    ------
        ValueError: There are no events or more than six, a time is not on
            a quarter of an hour or not after the one before it on the same
            day, or a temperature is outside the limits.

    """
    if not 1 <= len(events) <= SCHEDULE_SLOTS:
        msg = f"A day needs between 1 and {SCHEDULE_SLOTS} events."
        raise ValueError(msg)
    previous = -1
    for seconds, temperature in events:
        if not 0 <= seconds < _DAY or seconds % SCHEDULE_STEP:
            msg = f"{_hours(seconds)}: times must be on a quarter of an hour."
            raise ValueError(msg)
        if seconds <= previous:
            msg = (
                f"{_hours(seconds)}: the events of a day must be in order, "
                "and before midnight."
            )
            raise ValueError(msg)
        previous = seconds
        _check_temperature(temperature, minimum, maximum)


def _check_temperature(
    temperature: int, minimum: int | None, maximum: int | None
) -> None:
    """Check the temperature of an event against the limits that are known.

    Raises
    ------
        ValueError: The temperature is outside the limits, or 0 °C or below.

    """
    if minimum is not None and maximum is not None:
        limits = f"between {minimum / 100:g} and {maximum / 100:g} °C"
        fits = minimum <= temperature <= maximum
    elif minimum is not None:
        limits = f"at least {minimum / 100:g} °C"
        fits = temperature >= minimum
    elif temperature <= 0:
        limits = "above 0 °C"
        fits = False
    elif maximum is not None:
        limits = f"at most {maximum / 100:g} °C"
        fits = temperature <= maximum
    else:
        return
    if not fits:
        msg = f"{temperature / 100:g} °C: temperatures must be {limits}."
        raise ValueError(msg)


def changed_schedule(
    schedule: bytes,
    days: Iterable[int],
    events: Sequence[tuple[int, int]],
    moment: datetime | None = None,
) -> bytes:
    """Return a schedule with the events of some days replaced.

    The events go into the places of a day in order, and the places that are
    left over are switched off. The first event goes into the first place,
    which the thermostat keeps active. The other places are chosen to change
    as little as possible: an event that is already there keeps its place.

    Only the fields that change are written. Every other byte, the other
    days, and the time and temperature of a place that is switched off stay
    as the thermostat sent them. The schedule gets a new time if anything
    changed.

    Args:
    ----
        schedule: The schedule (field 8) the thermostat sent last.
        days: The days to change, 0 for Monday to 6 for Sunday.
        events: The events each of those days gets, see check_events().
        moment: The time of the change as a timezone-aware moment; now when
            omitted.

    Returns:
    -------
        The encoded schedule; the same bytes if nothing changes.

    Raises:
    ------
        ValueError: The days or events are not valid, or the schedule is not
            understood, see decode_schedule().

    """
    numbers = {day + 1 for day in check_days(days)}
    check_events(events)
    week = _week(schedule)
    current = _read_days(week)
    changed_days = []
    for day in _contents(Message.parse(week), _WEEK_DAY):
        number = Message.parse(day).varint(_DAY_NUMBER)
        # _read_days() made sure every day has a number.
        if number is None or number not in numbers:
            changed_days.append(day)
        else:
            changed_days.append(_changed_day(day, current[number], events))
    changed_week = set_repeated(week, _WEEK_DAY, changed_days)
    if changed_week == week:
        return schedule
    changed = set_field(schedule, _SCHEDULE_WEEK, changed_week)
    return set_field(changed, _SCHEDULE_TIME, device_time(moment))


def schedule_command(
    sequence: int,
    state: bytes,
    schedule: bytes,
    moment: datetime | None = None,
) -> bytes:
    """Build the message that makes the thermostat take over a schedule.

    The state goes along as the thermostat sent it, with the time of the
    change: mode and setpoints stay as they are.

    Args:
    ----
        sequence: The sequence number, which the thermostat acknowledges.
        state: The state (field 9) the thermostat sent last.
        schedule: The schedule to take over, see changed_schedule().
        moment: The time of the change as a timezone-aware moment; now when
            omitted.

    Returns:
    -------
        The encoded message.

    Raises:
    ------
        ValueError: The state is malformed.

    """
    return command(sequence, stamped_state(state, moment), schedule)


def stamped_state(state: bytes, moment: datetime | None = None) -> bytes:
    """Return the state of a thermostat with a new time of the change.

    Raises
    ------
        ValueError: The state is malformed.

    """
    return set_field(state, _STATE_TIME, device_time(moment))


def _week(schedule: bytes) -> bytes:
    """Return the week of a schedule.

    Raises
    ------
        ValueError: The schedule is malformed or has no week.

    """
    week = Message.parse(schedule).data(_SCHEDULE_WEEK)
    if week is None:
        msg = "The schedule has no week"
        raise ValueError(msg)
    return week


def _contents(message: Message, number: int) -> list[bytes]:
    """Return the content of every occurrence of a field that holds a message.

    Raises
    ------
        ValueError: An occurrence does not hold a message.

    """
    contents = []
    for field in message.fields:
        if field.number != number:
            continue
        if field.wire_type != LENGTH_DELIMITED or not isinstance(field.value, bytes):
            msg = f"Field {number} of the schedule does not hold a message"
            raise ValueError(msg)
        contents.append(field.value)
    return contents


def _read_days(week: bytes) -> dict[int, list[_Slot]]:
    """Read the days of a week by their number, 1 for Monday to 7 for Sunday.

    Raises
    ------
        ValueError: The week is malformed, or does not have seven days of six
            events each.

    """
    days: dict[int, list[_Slot]] = {}
    for day in _contents(Message.parse(week), _WEEK_DAY):
        fields = Message.parse(day)
        number = fields.varint(_DAY_NUMBER)
        if number is None or not 1 <= number <= 7 or number in days:
            msg = f"The schedule has a day numbered {number}"
            raise ValueError(msg)
        slots = [_read_slot(event) for event in _contents(fields, _DAY_EVENT)]
        if len(slots) != SCHEDULE_SLOTS:
            msg = f"A day of the schedule has {len(slots)} events"
            raise ValueError(msg)
        days[number] = slots
    if len(days) != 7:
        msg = f"The schedule has {len(days)} days"
        raise ValueError(msg)
    return days


def _read_slot(event: bytes) -> _Slot:
    """Read an event of a day.

    The thermostat writes every field, also one that is 0; a field that is
    left out counts as 0 all the same, as protobuf has it.

    Raises
    ------
        ValueError: The event is malformed.

    """
    fields = Message.parse(event)
    clock_data = fields.data(_EVENT_CLOCK)
    if clock_data is None:
        msg = "An event of the schedule has no time"
        raise ValueError(msg)
    clock = Message.parse(clock_data)
    hour, minute, second = (clock.varint(number) or 0 for number in _CLOCK_FIELDS)
    temperature = fields.varint(_EVENT_TEMPERATURE) or 0
    active = fields.varint(_EVENT_ACTIVE) or 0
    if hour >= 24 or minute >= 60 or second >= 60 or active > 1:
        msg = "An event of the schedule is not understood"
        raise ValueError(msg)
    return _Slot(hour * 3600 + minute * 60 + second, temperature, bool(active))


def _places(slots: list[_Slot], events: Sequence[tuple[int, int]]) -> tuple[int, ...]:
    """Choose the places of a day that the events go into, in order.

    The first event goes into the first place. Of the ways to place the
    others, the one that changes least wins: a place with the same time
    counts most, then one that is active, then one with the same temperature.
    """

    def score(places: tuple[int, ...]) -> int:
        return sum(
            4 * (slots[place].seconds == seconds)
            + 2 * slots[place].active
            + (slots[place].temperature == temperature)
            for place, (seconds, temperature) in zip(places, events, strict=True)
        )

    return max(
        ((0, *rest) for rest in combinations(range(1, len(slots)), len(events) - 1)),
        key=score,
    )


def _changed_day(
    day: bytes, slots: list[_Slot], events: Sequence[tuple[int, int]]
) -> bytes:
    """Return a day of the schedule with other events, see changed_schedule()."""
    places = dict(zip(_places(slots, events), events, strict=True))
    changed_events = [
        _changed_event(event, active=place in places, at=places.get(place))
        for place, event in enumerate(_contents(Message.parse(day), _DAY_EVENT))
    ]
    return set_repeated(day, _DAY_EVENT, changed_events)


def _changed_event(
    event: bytes, *, active: bool, at: tuple[int, int] | None = None
) -> bytes:
    """Return an event switched on or off, and at another time and temperature.

    Only a field that changes is written.

    Args:
    ----
        event: The encoded event.
        active: Whether the event is switched on.
        at: The new time in seconds since midnight and the new temperature in
            1/100 °C; None keeps both.

    Returns:
    -------
        The encoded event.

    """
    fields = Message.parse(event)
    if at is not None:
        seconds, temperature = at
        clock = fields.data(_EVENT_CLOCK) or b""
        current = Message.parse(clock)
        changed_clock = clock
        values = (seconds // 3600, seconds % 3600 // 60, seconds % 60)
        for number, value in zip(_CLOCK_FIELDS, values, strict=True):
            if (current.varint(number) or 0) != value:
                changed_clock = set_field(changed_clock, number, value)
        if changed_clock != clock:
            event = set_field(event, _EVENT_CLOCK, changed_clock)
        if (fields.varint(_EVENT_TEMPERATURE) or 0) != temperature:
            event = set_field(event, _EVENT_TEMPERATURE, temperature)
    if bool(fields.varint(_EVENT_ACTIVE)) != active:
        event = set_field(event, _EVENT_ACTIVE, int(active))
    return event


def _clock(seconds: int) -> str:
    """Show a time of the schedule as HH:MM:SS."""
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _hours(seconds: int) -> str:
    """Show a time of the schedule as HH:MM, for a message to a user."""
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


def response_field(message: Message) -> int | None:
    """Tell whether a message is one of the two answers that follow a login.

    Returns
    -------
        FIELD_CONFIGURATION or FIELD_SUPPORT if the message is that answer and
        nothing else, and None for any other message.

    """
    for number in (FIELD_CONFIGURATION, FIELD_SUPPORT):
        if (
            message.numbers <= {FIELD_SEQUENCE, _FIELD_5, number}
            and message.count(number) == 1
            and message.data(number) is not None
        ):
            return number
    return None


def describe(message: Message) -> str:
    """Summarise a message for debug logs.

    A login is named but never shown: it holds the account's email address.
    """
    if FIELD_LOGIN in message:
        return "login"
    parts = [
        f"seq={message.varint(FIELD_SEQUENCE)}",
        "fields=" + ",".join(str(number) for number in sorted(message.numbers)),
    ]
    kind = message.varint(FIELD_KIND)
    if kind is not None:
        parts.append(f"kind={kind}")
    answer = message.varint(FIELD_ACKNOWLEDGED)
    if answer is not None:
        # 1 accepts the message this answers; anything else refuses it.
        parts.append(f"answer={answer}")
    try:
        parts += _describe_state(message)
        parts += _describe_device_info(message)
        parts += _describe_telemetry(message)
    except ValueError:
        parts.append("undecodable")
    return " ".join(parts)


def _hold_setpoints(state: Message) -> dict[int, int]:
    """Return the setpoint each hold stores, by the field of the hold."""
    setpoints: dict[int, int] = {}
    for hold in (_TEMPORARY_HOLD, _PERMANENT_HOLD):
        fields = state.message(hold)
        setpoint = None if fields is None else fields.varint(_HOLD_SETPOINT)
        if setpoint is not None:
            setpoints[hold] = setpoint
    return setpoints


def _celsius(setpoint: int) -> str:
    """Show a setpoint from the wire in Celsius and Fahrenheit."""
    celsius = setpoint / SETPOINT_SCALE
    return f"{celsius:.2f}C/{celsius * 9 / 5 + 32:.1f}F"


def _describe_state(message: Message) -> list[str]:
    state = message.message(FIELD_STATE)
    if state is None:
        return []
    mode = state.varint(_STATE_MODE)
    name = "unknown" if mode is None else _MODE_NAMES.get(mode, "unknown")
    parts = [f"mode={mode} ({name})"]
    setpoints = _hold_setpoints(state)
    if _TEMPORARY_HOLD in setpoints:
        parts.append(f"temporary={_celsius(setpoints[_TEMPORARY_HOLD])}")
        hold = state.message(_TEMPORARY_HOLD)
        until = None if hold is None else hold.varint(_HOLD_END_TIME)
        if until is not None:
            try:
                end = EPOCH + timedelta(seconds=until)
            except OverflowError:
                # An end that no calendar holds, such as "never".
                parts.append(f"until={until}")
            else:
                parts.append(f"until={end:%Y-%m-%d %H:%M:%SZ}")
    if _PERMANENT_HOLD in setpoints:
        parts.append(f"permanent={_celsius(setpoints[_PERMANENT_HOLD])}")
    shown = {_STATE_TIME, _STATE_MODE, _TEMPORARY_HOLD, _PERMANENT_HOLD}
    for field in sorted(state.fields, key=lambda field: field.number):
        if field.number not in shown:
            shown.add(field.number)
            parts.append(f"{FIELD_STATE}.{field.number}={brief(field.value)}")
    return parts


def _describe_device_info(message: Message) -> list[str]:
    info = message.message(FIELD_DEVICE_INFO)
    target = None if info is None else info.varint(_INFO_TARGET)
    return [] if target is None else [f"target={_celsius(target)}"]


def _describe_telemetry(message: Message) -> list[str]:
    telemetry = message.message(FIELD_TELEMETRY)
    if telemetry is None:
        return []
    parts = []
    floor = telemetry.varint(_TELEMETRY_FLOOR)
    if floor is not None:
        # The floor temperature is in 1/100 °C, half the scale of a setpoint.
        parts.append(f"floor={_celsius(floor * 2)}")
    heating = telemetry.varint(_TELEMETRY_HEATING)
    if heating is not None:
        parts.append(f"heating={heating}")
    return parts
