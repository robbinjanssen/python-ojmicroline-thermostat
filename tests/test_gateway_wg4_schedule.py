"""Test the schedule of WG4-series thermostats on the gateway.

The layout of the schedule is as reported in issue 480 of the Home Assistant
integration, for one thermostat with firmware 1012M201. The schedules here are
made up in that layout, with fields this library does not know at every level:
those have to go back to the thermostat byte for byte.

The temperatures of the schedule are in 1/100 °C on the wire, unlike the
setpoints of a hold (1/200 °C). Getting that wrong writes double or half the
temperature, so it is pinned here.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

import pytest
from freezegun import freeze_time

from ojmicroline_thermostat import (
    OJMicrolineConnectionError,
    OJMicrolineError,
    OJMicrolineTimeoutError,
    OJMicrolineUnconfirmedError,
)
from ojmicroline_thermostat.gateway import GatewayMode, WG4Gateway, server_ssl_context
from ojmicroline_thermostat.gateway import server as gateway_server
from ojmicroline_thermostat.gateway import wg4_gateway as gateway_wg4
from ojmicroline_thermostat.gateway.protobuf import (
    Message,
    encode_field,
    set_field,
    set_repeated,
)
from ojmicroline_thermostat.gateway.wg4 import (
    changed_schedule,
    check_days,
    check_events,
    decode_schedule,
    decode_schedule_fields,
    device_time,
    schedule_command,
    stamped_state,
)

from .gateway_fakes import (
    PROFILE,
    SCHEDULE,
    SCHEDULE_HOURS,
    SERIAL,
    STATE,
    Events,
    FakeThermostat,
    acknowledgement,
    schedule_event,
    schedule_wire,
    until,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path

HOUR = 3600
MOMENT = datetime(2026, 11, 23, 12, tzinfo=UTC)
MONDAY, SATURDAY, SUNDAY = 0, 5, 6


def _days(schedule: bytes) -> list[bytes]:
    """Return the days of a schedule as they are encoded."""
    week = Message.parse(schedule).data(2)
    assert week is not None
    return [
        field.value
        for field in Message.parse(week).fields
        if field.number == 1 and isinstance(field.value, bytes)
    ]


def _events(day: bytes) -> list[bytes]:
    """Return the events of a day as they are encoded."""
    return [
        field.value
        for field in Message.parse(day).fields
        if field.number == 2 and isinstance(field.value, bytes)
    ]


def _active(schedule: bytes, day: int) -> list[tuple[str, int]]:
    """Return the time and temperature of the active events of a day."""
    return [
        (event["Clock"], event["TempFloor"])
        for event in decode_schedule(schedule)[day]["Events"]
        if event["Active"]
    ]


def _with_week(week: bytes) -> bytes:
    return encode_field(1, 1) + encode_field(2, week)


def _week_of(*days: bytes) -> bytes:
    return b"".join(encode_field(1, day) for day in days)


def _day(number: int, events: list[bytes] | None = None) -> bytes:
    if events is None:
        events = [schedule_event(hour, 2000, active=True) for hour in SCHEDULE_HOURS]
    return encode_field(1, number) + b"".join(encode_field(2, e) for e in events)


# --- Reading the schedule -----------------------------------------------------


def test_decode_schedule() -> None:
    """Every day comes Monday first, with its six events in 1/100 °C."""
    days = decode_schedule(schedule_wire())
    assert [day["WeekDayGrpNo"] for day in days] == [1, 2, 3, 4, 5, 6, 7]
    assert days[0]["Events"][0] == {
        "ScheduleType": 0,
        "Clock": "06:00:00",
        "TempFloor": 1710,
        "Active": True,
    }
    assert days[6]["Events"][5] == {
        "ScheduleType": 5,
        "Clock": "22:00:00",
        "TempFloor": 1775,
        "Active": True,
    }
    # An event that is switched off keeps its time and temperature.
    assert days[0]["Events"][2] == {
        "ScheduleType": 2,
        "Clock": "12:00:00",
        "TempFloor": 1712,
        "Active": False,
    }


def test_decode_schedule_in_any_order() -> None:
    """The days are sorted by their number, whatever order they came in."""
    week = _week_of(*(_day(number) for number in (7, 1, 2, 3, 4, 5, 6)))
    assert [day["WeekDayGrpNo"] for day in decode_schedule(_with_week(week))] == [
        1,
        2,
        3,
        4,
        5,
        6,
        7,
    ]


def test_decode_schedule_counts_missing_fields_as_zero() -> None:
    """A field that is left out is 0, as protobuf has it."""
    bare = encode_field(1, encode_field(1, 7))
    events = [bare] + [
        schedule_event(hour, 2000, active=True) for hour in SCHEDULE_HOURS[1:]
    ]
    week = _week_of(_day(1, events), *(_day(number) for number in range(2, 8)))
    assert decode_schedule(_with_week(week))[0]["Events"][0] == {
        "ScheduleType": 0,
        "Clock": "07:00:00",
        "TempFloor": 0,
        "Active": False,
    }


def _seven(first: bytes) -> bytes:
    """Return a schedule whose Monday is given, and whose other days are fine."""
    return _with_week(_week_of(first, *(_day(number) for number in range(2, 8))))


def _events_of(first: bytes) -> bytes:
    """Return a Monday whose first event is given."""
    rest = [schedule_event(hour, 2000, active=True) for hour in SCHEDULE_HOURS[1:]]
    return _day(1, [first, *rest])


@pytest.mark.parametrize(
    ("schedule", "error"),
    [
        (encode_field(1, 1), "no week"),
        (_with_week(_week_of(*(_day(n) for n in range(1, 7)))), "has 6 days"),
        (_with_week(_week_of(*(_day(n) for n in (1, 1, 2, 3, 4, 5, 6)))), "numbered 1"),
        (_with_week(_week_of(*(_day(n) for n in range(2, 9)))), "numbered 8"),
        (_seven(encode_field(2, b"")), "numbered None"),
        (_seven(_day(1, [schedule_event(6, 2000, active=True)])), "has 1 events"),
        (_seven(_events_of(encode_field(2, 2000))), "has no time"),
        (_seven(_events_of(schedule_event(24, 2000, active=True))), "not understood"),
        (
            _seven(
                _events_of(schedule_event(6, 2000, active=True, minute=60)),
            ),
            "not understood",
        ),
        (_seven(_events_of(encode_field(1, b"") + encode_field(3, 2))), "not under"),
        (_with_week(encode_field(1, 5)), "does not hold a message"),
        (b"\xff", "Truncated"),
    ],
)
def test_decode_schedule_rejects_what_is_not_understood(
    schedule: bytes, error: str
) -> None:
    """Only a week of seven days with six events each is read."""
    with pytest.raises(ValueError, match=error):
        decode_schedule(schedule)


def test_decode_schedule_fields() -> None:
    """A schedule that is not understood is left out, not refused."""
    upload = encode_field(8, schedule_wire()) + encode_field(9, STATE)
    assert decode_schedule_fields(Message.parse(upload)) == {
        "Schedules": decode_schedule(schedule_wire())
    }
    assert not decode_schedule_fields(Message.parse(encode_field(9, STATE)))
    assert not decode_schedule_fields(Message.parse(encode_field(8, SCHEDULE)))


# --- Checking a change ----------------------------------------------------------


def test_check_days() -> None:
    """The days are 0 for Monday to 6 for Sunday, each counted once."""
    assert check_days([6, 0, 6]) == frozenset({0, 6})
    for days in ([], [7], [-1]):
        with pytest.raises(ValueError, match="0 for Monday to 6 for Sunday"):
            check_days(days)


@pytest.mark.parametrize(
    ("events", "error"),
    [
        ([], "between 1 and 6 events"),
        ([(hour * HOUR, 2000) for hour in range(7)], "between 1 and 6 events"),
        ([(6 * HOUR + 600, 2000)], "06:10: times must be on a quarter"),
        ([(24 * HOUR, 2000)], "24:00: times must be on a quarter"),
        ([(-900, 2000)], "times must be on a quarter"),
        ([(6 * HOUR, 2000), (5 * HOUR, 2000)], "05:00: the events of a day must"),
        ([(6 * HOUR, 2000), (6 * HOUR, 1800)], "06:00: the events of a day must"),
        ([(6 * HOUR, 0)], "0 °C: temperatures must be above 0 °C"),
    ],
)
def test_check_events_rejects(events: list[tuple[int, int]], error: str) -> None:
    """A day has one to six events, in order, on quarters of an hour."""
    with pytest.raises(ValueError, match=error):
        check_events(events)


def test_check_events_limits() -> None:
    """A temperature has to be within the limits of the thermostat, if known."""
    check_events([(0, 500), (23 * HOUR + 2700, 4000)], 500, 4000)
    with pytest.raises(ValueError, match=r"4\.5 °C: temperatures must be between 5"):
        check_events([(0, 450)], 500, 4000)
    with pytest.raises(ValueError, match=r"40.5 °C: .* between 5 and 40 °C"):
        check_events([(0, 4050)], 500, 4000)
    # With one limit, the message names that one.
    with pytest.raises(ValueError, match=r"4\.5 °C: temperatures must be at least 5"):
        check_events([(0, 450)], 500, None)
    with pytest.raises(ValueError, match=r"40\.5 °C: temperatures must be at most 40"):
        check_events([(0, 4050)], None, 4000)
    with pytest.raises(ValueError, match="above 0 °C"):
        check_events([(0, -100)], None, 4000)
    check_events([(0, 9000)], 500, None)


# --- Changing the schedule ------------------------------------------------------


def test_unchanged_schedule_is_the_same() -> None:
    """Asking for the events a day has changes nothing, not even the time."""
    schedule = schedule_wire()
    monday = [(6 * HOUR, 1710), (9 * HOUR, 1711), (18 * HOUR, 1714), (22 * HOUR, 1715)]
    assert changed_schedule(schedule, [MONDAY], monday, MOMENT) == schedule


def test_changed_schedule_changes_only_what_was_asked() -> None:
    """One temperature on Sunday changes, and nothing else but the time."""
    schedule = schedule_wire()
    sunday = [(6 * HOUR, 1770), (9 * HOUR, 1771), (18 * HOUR, 1774), (22 * HOUR, 2050)]
    changed = changed_schedule(schedule, [SUNDAY], sunday, MOMENT)

    root = Message.parse(changed)
    assert root.varint(1) == device_time(MOMENT)
    # The fields this library does not know stay where they were.
    assert root.varint(9) == 77
    week = root.message(2)
    assert week is not None
    assert week.varint(7) == 1
    # The other days are the same bytes.
    assert _days(changed)[:6] == _days(schedule)[:6]
    before, after = _events(_days(schedule)[6]), _events(_days(changed)[6])
    assert after[:5] == before[:5]
    assert after[5] != before[5]
    assert _active(changed, SUNDAY) == [
        ("06:00:00", 1770),
        ("09:00:00", 1771),
        ("18:00:00", 1774),
        ("22:00:00", 2050),
    ]
    assert Message.parse(after[5]).data(11) == b"unknown event"
    assert Message.parse(_days(changed)[6]).varint(9) == 5

    # Changing it back gives the bytes of the original, but for the time.
    original = [
        (6 * HOUR, 1770),
        (9 * HOUR, 1771),
        (18 * HOUR, 1774),
        (22 * HOUR, 1775),
    ]
    restored = changed_schedule(changed, [SUNDAY], original, MOMENT)
    assert _days(restored) == _days(schedule)


def test_changed_schedule_on_several_days() -> None:
    """The same events go to every day that was chosen."""
    schedule = schedule_wire()
    events = [(hour * HOUR + 1800, 1900 + hour) for hour in (0, 4, 8, 12, 16, 20)]
    changed = changed_schedule(schedule, [SATURDAY, SUNDAY], events, MOMENT)
    expected = [(f"{hour:02d}:30:00", 1900 + hour) for hour in (0, 4, 8, 12, 16, 20)]
    assert _active(changed, SATURDAY) == expected
    assert _active(changed, SUNDAY) == expected
    assert _days(changed)[:5] == _days(schedule)[:5]


def test_changed_schedule_switches_places_off() -> None:
    """Places that are left over are switched off, and keep their values."""
    schedule = schedule_wire()
    changed = changed_schedule(schedule, [MONDAY], [(5 * HOUR, 2100)], MOMENT)
    events = decode_schedule(changed)[0]["Events"]
    assert events[0] == {
        "ScheduleType": 0,
        "Clock": "05:00:00",
        "TempFloor": 2100,
        "Active": True,
    }
    assert [event["Active"] for event in events[1:]] == [False] * 5
    assert events[5]["Clock"] == "22:00:00"
    assert events[5]["TempFloor"] == 1715


def test_changed_schedule_keeps_an_event_in_its_place() -> None:
    """An event that is there already, active or not, keeps its place."""
    schedule = schedule_wire()
    # 14:00 is in the fourth place, which is switched off. It is chosen over
    # the active second place, whose time is another.
    changed = changed_schedule(
        schedule, [MONDAY], [(6 * HOUR, 1710), (14 * HOUR, 2000)], MOMENT
    )
    events = decode_schedule(changed)[0]["Events"]
    assert [event["Active"] for event in events] == [
        True,
        False,
        False,
        True,
        False,
        False,
    ]
    assert events[3]["TempFloor"] == 2000
    # Without a match, the active places come first.
    changed = changed_schedule(
        schedule, [MONDAY], [(6 * HOUR, 1710), (15 * HOUR, 2000)], MOMENT
    )
    events = decode_schedule(changed)[0]["Events"]
    assert [event["Active"] for event in events] == [
        True,
        True,
        False,
        False,
        False,
        False,
    ]
    assert events[1]["Clock"] == "15:00:00"


def test_changed_schedule_writes_a_missing_field() -> None:
    """A field the thermostat left out is written when its value changes."""
    bare = encode_field(1, encode_field(1, 6)) + encode_field(2, 2000)
    rest = [schedule_event(hour, 2000, active=False) for hour in SCHEDULE_HOURS[1:]]
    week = _week_of(_day(1, [bare, *rest]), *(_day(n) for n in range(2, 8)))
    changed = changed_schedule(
        _with_week(week), [MONDAY], [(6 * HOUR + 900, 2000)], MOMENT
    )
    assert decode_schedule(changed)[0]["Events"][0] == {
        "ScheduleType": 0,
        "Clock": "06:15:00",
        "TempFloor": 2000,
        "Active": True,
    }


def test_changed_schedule_refuses() -> None:
    """Days and events that are not valid, or a schedule that is not understood."""
    with pytest.raises(ValueError, match="0 for Monday"):
        changed_schedule(schedule_wire(), [7], [(0, 2000)])
    with pytest.raises(ValueError, match="between 1 and 6 events"):
        changed_schedule(schedule_wire(), [0], [])
    with pytest.raises(ValueError, match="no week"):
        changed_schedule(SCHEDULE, [0], [(0, 2000)])


@freeze_time("2026-11-23 12:00:00")
def test_changed_schedule_defaults_to_now() -> None:
    """The time of the change is now when it is not given."""
    changed = changed_schedule(schedule_wire(), [MONDAY], [(0, 2000)])
    assert Message.parse(changed).varint(1) == device_time(MOMENT)
    state = Message.parse(stamped_state(STATE))
    assert state.varint(1) == device_time(MOMENT)


def test_schedule_command() -> None:
    """The command holds the schedule, and the state with a new time."""
    changed = changed_schedule(schedule_wire(), [MONDAY], [(0, 2000)], MOMENT)
    sent = Message.parse(schedule_command(105, STATE, changed, MOMENT))
    assert sent.numbers == {1, 5, 8, 9}
    assert sent.varint(1) == 105
    assert sent.data(8) == changed
    state = sent.message(9)
    assert state is not None
    assert state.varint(1) == device_time(MOMENT)
    # Mode and setpoints stay as the thermostat sent them.
    original = Message.parse(STATE)
    assert [f for f in state.fields if f.number != 1] == [
        f for f in original.fields if f.number != 1
    ]


# --- The gateway ----------------------------------------------------------------


@pytest.fixture(name="gateway")
async def gateway_fixture(
    certificate: tuple[Path, Path], events: Events
) -> AsyncIterator[WG4Gateway]:
    """Run a gateway on a free port that answers one thermostat itself."""
    gateway = WG4Gateway(
        server_ssl_context(*certificate),
        mode=GatewayMode.LOCAL,
        profiles={SERIAL: PROFILE},
        listen_host="127.0.0.1",
        listen_port=0,
    )
    gateway.allowed_serials = {SERIAL}
    gateway.on_state = lambda serial, fields: events.append((serial, fields))
    await gateway.start()
    yield gateway
    await gateway.stop()


async def _connected(
    connect: Callable[[], Any], schedule: bytes | None = None
) -> FakeThermostat:
    """Connect a thermostat that has logged in and uploaded a schedule."""
    thermostat: FakeThermostat = await connect()
    await thermostat.login()
    await thermostat.upload(schedule_wire() if schedule is None else schedule)
    return thermostat


SUNDAY_EVENTS = [(6 * HOUR, 2000), (22 * HOUR, 1800)]


async def _send_schedule(
    gateway: WG4Gateway, thermostat: FakeThermostat
) -> tuple[asyncio.Task[None], bytes]:
    """Start a change of Sunday, and accept it as the thermostat.

    Returns the task of the change and the schedule the thermostat was sent.
    """
    task = asyncio.create_task(gateway.set_schedule(SERIAL, [SUNDAY], SUNDAY_EVENTS))
    sent = await thermostat.receive()
    schedule = sent.data(8)
    assert schedule is not None
    await thermostat.send(acknowledgement(sent.varint(1) or 0))
    # The gateway makes the thermostat connect anew.
    assert await thermostat.was_closed()
    return task, schedule


async def test_set_schedule(
    gateway: WG4Gateway, events: Events, connect: Callable[[], Any]
) -> None:
    """A schedule counts once the thermostat uploads it on a new connection."""
    thermostat = await _connected(connect)
    assert (SERIAL, {"Schedules": decode_schedule(schedule_wire())}) in [
        (serial, {"Schedules": fields["Schedules"]})
        for serial, fields in events
        if "Schedules" in fields
    ]

    task = asyncio.create_task(gateway.set_schedule(SERIAL, [SUNDAY], SUNDAY_EVENTS))
    sent = await thermostat.receive()
    assert sent.numbers == {1, 5, 8, 9}
    assert sent.varint(1) == 101
    schedule = sent.data(8)
    assert schedule is not None
    assert _active(schedule, SUNDAY) == [("06:00:00", 2000), ("22:00:00", 1800)]
    assert _days(schedule)[:6] == _days(schedule_wire())[:6]
    state = sent.message(9)
    assert state is not None
    # The mode and the setpoints stay; only the time of the state is new.
    assert state.varint(2) == 3
    assert state.message(5) == Message.parse(encode_field(1, 4444))
    assert state.varint(1) != 1000
    assert not task.done()

    await thermostat.send(acknowledgement(101))
    # The thermostat reports the schedule it accepted right away.
    accepted = (SERIAL, {"Schedules": decode_schedule(schedule)})
    await until(lambda: accepted in events)
    assert await thermostat.was_closed()
    await until(lambda: SERIAL not in gateway.connected)
    assert not task.done()

    again = await _connected(connect, schedule)
    await task
    assert gateway.history[-1].endswith(f"Thermostat {SERIAL} kept the new schedule")
    assert SERIAL not in gateway._readbacks  # pylint: disable=protected-access

    # The next command starts from the new schedule.
    mode = asyncio.create_task(gateway.set_mode(SERIAL, 1))
    sent = await again.receive()
    assert sent.data(8) == schedule
    await again.send(acknowledgement(sent.varint(1) or 0))
    await mode


async def test_set_schedule_that_does_not_change(
    gateway: WG4Gateway, connect: Callable[[], Any]
) -> None:
    """Nothing is sent when the schedule already has the events."""
    thermostat = await _connected(connect)
    monday = [(6 * HOUR, 1710), (9 * HOUR, 1711), (18 * HOUR, 1714), (22 * HOUR, 1715)]
    await gateway.set_schedule(SERIAL, [MONDAY], monday)
    assert gateway.history[-1].endswith(
        f"The schedule of thermostat {SERIAL} does not change"
    )

    # The next command is the first one the thermostat gets.
    task = asyncio.create_task(gateway.set_mode(SERIAL, 1))
    sent = await thermostat.receive()
    assert sent.varint(1) == 101
    await thermostat.send(acknowledgement(101))
    await task


async def test_set_schedule_that_was_not_kept(
    gateway: WG4Gateway, connect: Callable[[], Any]
) -> None:
    """A thermostat that uploads the old schedule did not keep the new one."""
    thermostat = await _connected(connect)
    task, _ = await _send_schedule(gateway, thermostat)
    await _connected(connect)
    with pytest.raises(OJMicrolineError, match="did not keep the schedule"):
        await task
    assert "uploaded another schedule than it accepted" in gateway.history[-1]


async def test_set_schedule_that_is_not_confirmed(
    gateway: WG4Gateway,
    connect: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A thermostat that does not upload its schedule in time is not confirmed."""
    monkeypatch.setattr(gateway_wg4, "READBACK_TIMEOUT", 0.05)
    thermostat = await _connected(connect)
    task, _ = await _send_schedule(gateway, thermostat)
    with pytest.raises(OJMicrolineUnconfirmedError, match="did not confirm"):
        await task
    assert gateway.history[-1].endswith(
        f"Thermostat {SERIAL} did not upload its schedule within 0.05 seconds"
    )
    assert SERIAL not in gateway._readbacks  # pylint: disable=protected-access


async def test_set_schedule_read_back_is_not_understood(
    gateway: WG4Gateway, connect: Callable[[], Any]
) -> None:
    """A schedule that comes back in a form that is not understood is an error."""
    thermostat = await _connected(connect)
    task, _ = await _send_schedule(gateway, thermostat)
    await _connected(connect, SCHEDULE)
    with pytest.raises(OJMicrolineError, match="not understood"):
        await task


async def test_set_schedule_refuses(
    gateway: WG4Gateway, connect: Callable[[], Any]
) -> None:
    """A change that cannot be built fails before anything is sent."""
    with pytest.raises(ValueError, match="between 1 and 6 events"):
        await gateway.set_schedule(SERIAL, [SUNDAY], [])
    with pytest.raises(ValueError, match="0 for Monday"):
        await gateway.set_schedule(SERIAL, [], SUNDAY_EVENTS)
    with pytest.raises(OJMicrolineConnectionError, match="is not connected"):
        await gateway.set_schedule(SERIAL, [SUNDAY], SUNDAY_EVENTS)

    # Connected, but the schedule is not there yet.
    thermostat: FakeThermostat = await connect()
    await thermostat.login()
    with pytest.raises(OJMicrolineConnectionError, match="not reported its schedule"):
        await gateway.set_schedule(SERIAL, [SUNDAY], SUNDAY_EVENTS)

    # A schedule this library does not understand is not changed.
    await thermostat.upload(SCHEDULE)
    with pytest.raises(OJMicrolineError, match="schedule of the thermostat is not"):
        await gateway.set_schedule(SERIAL, [SUNDAY], SUNDAY_EVENTS)


async def test_read_back_waits_for_a_new_connection(gateway: WG4Gateway) -> None:
    """Only a schedule from another connection than the old one counts."""
    loop = asyncio.get_running_loop()
    old = Mock(serial=SERIAL, state=None, schedule=None)
    new = Mock(serial=SERIAL, state=None, schedule=None)
    waiter: asyncio.Future[bytes] = loop.create_future()
    gateway._readbacks[SERIAL] = (old, waiter)  # pylint: disable=protected-access
    upload = Message.parse(encode_field(8, schedule_wire()))

    gateway._observe(old, upload)  # pylint: disable=protected-access
    assert not waiter.done()
    gateway._observe(new, upload)  # pylint: disable=protected-access
    assert waiter.result() == schedule_wire()
    # A second upload leaves a waiter that has its schedule alone.
    gateway._observe(new, Message.parse(encode_field(8, SCHEDULE)))  # pylint: disable=protected-access
    assert waiter.result() == schedule_wire()


async def test_set_schedule_compares_only_active_events(
    gateway: WG4Gateway, connect: Callable[[], Any]
) -> None:
    """What a place that is switched off holds does not count."""
    thermostat = await _connected(connect)
    task, schedule = await _send_schedule(gateway, thermostat)
    # The thermostat clears the places it does not use when it stores them.
    sunday = _days(schedule)[6]
    places = _events(sunday)
    cleared = [
        event
        if Message.parse(event).varint(3)
        else schedule_event(0, 500, active=False)
        for event in places
    ]
    kept = set_repeated(sunday, 2, cleared)
    week = Message.parse(schedule).data(2)
    assert week is not None
    stored = set_field(schedule, 2, set_repeated(week, 1, [*_days(schedule)[:6], kept]))
    assert stored != schedule
    await _connected(connect, stored)
    await task


async def test_command_waiting_during_a_read_back_is_not_sent(
    gateway: WG4Gateway, connect: Callable[[], Any]
) -> None:
    """A command that waited for the schedule is not sent on the old connection."""
    thermostat = await _connected(connect)
    task = asyncio.create_task(gateway.set_schedule(SERIAL, [SUNDAY], SUNDAY_EVENTS))
    sent = await thermostat.receive()
    mode = asyncio.create_task(gateway.set_mode(SERIAL, 1))
    await asyncio.sleep(0.01)
    assert not mode.done()

    await thermostat.send(acknowledgement(sent.varint(1) or 0))
    with pytest.raises(OJMicrolineConnectionError, match="disconnected"):
        await mode
    # The connection closed without another command on it.
    assert await thermostat.was_closed()
    schedule = sent.data(8)
    assert schedule is not None
    await _connected(connect, schedule)
    await task


async def test_late_acknowledgement_of_a_schedule(
    gateway: WG4Gateway,
    events: Events,
    connect: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A schedule that is accepted after the wait ended keeps the connection."""
    monkeypatch.setattr(gateway_server, "ACKNOWLEDGE_TIMEOUT", 0.05)
    monkeypatch.setattr(gateway_server, "tcp_state", lambda _writer: "")
    thermostat = await _connected(connect)
    with pytest.raises(OJMicrolineTimeoutError, match="did not acknowledge"):
        await gateway.set_schedule(SERIAL, [SUNDAY], SUNDAY_EVENTS)
    sent = await thermostat.receive()
    schedule = sent.data(8)
    assert schedule is not None

    await thermostat.send(acknowledgement(sent.varint(1) or 0))
    accepted = (SERIAL, {"Schedules": decode_schedule(schedule)})
    await until(lambda: accepted in events)
    assert SERIAL in gateway.connected
    assert SERIAL not in gateway._readbacks  # pylint: disable=protected-access

    # The next command starts from the schedule the thermostat accepted.
    monkeypatch.setattr(gateway_server, "ACKNOWLEDGE_TIMEOUT", 2)
    mode = asyncio.create_task(gateway.set_mode(SERIAL, 1))
    sent = await thermostat.receive()
    assert sent.data(8) == schedule
    await thermostat.send(acknowledgement(sent.varint(1) or 0))
    await mode


async def test_stop_ends_a_read_back(
    gateway: WG4Gateway, connect: Callable[[], Any]
) -> None:
    """A read-back does not outlive the gateway."""
    thermostat = await _connected(connect)
    task, _ = await _send_schedule(gateway, thermostat)
    await until(lambda: SERIAL in gateway._readbacks)  # pylint: disable=protected-access
    await gateway.stop()
    with pytest.raises(OJMicrolineConnectionError, match="gateway stopped"):
        await task
    assert not gateway._readbacks  # pylint: disable=protected-access
