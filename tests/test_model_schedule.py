"""Test the models."""
import json
from collections.abc import Sequence
from typing import Any

import pytest
from freezegun import freeze_time

from ojmicroline_thermostat.models import Schedule

from . import load_fixtures


@pytest.mark.asyncio
async def test_schedule_from_json() -> None:
    """Make sure the data is accepted by the from_json method."""
    fixture = load_fixtures("schedule.json")
    data = json.loads(fixture)
    schedule = Schedule.from_json(data)

    assert schedule.days is not None
    for _, day in schedule.days.items():
        assert isinstance(day, Sequence)
        for event in day:
            assert event.temperature is not None
            assert event.date is not None


@pytest.mark.asyncio
async def test_schedule_from_json_skip_inactive() -> None:
    """Skip inactive entries in the schedule."""
    fixture = load_fixtures("schedule.json")
    data = json.loads(fixture)
    schedule = Schedule.from_json(data)
    assert len(schedule.days[0]) == 4

    data["Days"][0]["Events"][0]["Active"] = False
    schedule = Schedule.from_json(data)
    assert len(schedule.days[0]) == 3


@pytest.mark.asyncio
@freeze_time("2023-01-05 06:00")
async def test_schedule_from_json_convert_days() -> None:
    """Make sure the days are converted properly."""
    data = {"Days": [{"WeekDayGrpNo": 0, "Events": []}]}

    schedule = Schedule.from_json(data)
    assert schedule.days[6] is not None


@pytest.mark.asyncio
@freeze_time("2023-01-01 08:00")
async def test_schedule_get_active_temperaturea() -> None:
    """Test the get active temperature method."""
    fixture = load_fixtures("schedule.json")
    data = json.loads(fixture)
    schedule = Schedule.from_json(data)
    assert schedule.get_active_temperature() == 2350


@pytest.mark.asyncio
@freeze_time("2023-01-04 06:00")
async def test_schedule_get_active_temperature_use_event_from_prev_day() -> None:
    """
    Use event from prev day when time is before first event.

    WeekDayGrpNo 2 = Tuesday and WeekDayGrpNo 3 = Wednesday

    2023-01-04 = Wednesday, because the current time (06:00) is before the first
    event, the last event of tuesday is used.
    """
    data = {
        "Days": [
            {
                "WeekDayGrpNo": 2,
                "Events": [{"Clock": "08:30:00", "Temperature": 2500, "Active": True}],
            },
            {
                "WeekDayGrpNo": 3,
                "Events": [{"Clock": "08:30:00", "Temperature": 2600, "Active": True}],
            },
        ]
    }
    schedule = Schedule.from_json(data)
    assert schedule.get_active_temperature() == 2500


@pytest.mark.asyncio
@freeze_time("2023-01-02 06:00")
async def test_schedule_get_active_temperature_use_event_from_sunday() -> None:
    """
    Use sunday when monday and before first event.

    WeekDayGrpNo 0 = Sunday and WeekDayGrpNo 1 = Monday

    In python datetime 0 = Monday and 6 = Sunday.

    When the datetime.weekday() hits 0 (monday) and the time is before the first
    event, then the model should get 6 (sunday) of the event. In this case week
    day grp 0.
    """
    data = {
        "Days": [
            {
                "WeekDayGrpNo": 0,
                "Events": [{"Clock": "08:30:00", "Temperature": 2500, "Active": True}],
            },
            {
                "WeekDayGrpNo": 1,
                "Events": [{"Clock": "08:30:00", "Temperature": 2600, "Active": True}],
            },
        ]
    }
    schedule = Schedule.from_json(data)
    assert schedule.get_active_temperature() == 2500


@pytest.mark.asyncio
@freeze_time("2023-01-02 06:00")
async def test_schedule_get_lowest_temperature() -> None:
    """Test that the lowest temperature from the schedule is returned."""
    data = {
        "Days": [
            {
                "WeekDayGrpNo": 0,
                "Events": [{"Clock": "08:30:00", "Temperature": 2500, "Active": True}],
            },
            {
                "WeekDayGrpNo": 1,
                "Events": [{"Clock": "08:30:00", "Temperature": 900, "Active": True}],
            },
            {
                "WeekDayGrpNo": 2,
                "Events": [{"Clock": "08:30:00", "Temperature": 2600, "Active": True}],
            },
        ]
    }
    schedule = Schedule.from_json(data)
    assert schedule.get_lowest_temperature() == 900


@pytest.mark.asyncio
@freeze_time("2023-01-02 06:00")
async def test_schedule_get_lowest_temperature_is_zero() -> None:
    """Test that the 0 is returned when there are not temperatures."""
    data: dict[str, Any] = {"Days": []}
    schedule = Schedule.from_json(data)
    assert schedule.get_lowest_temperature() == 0
