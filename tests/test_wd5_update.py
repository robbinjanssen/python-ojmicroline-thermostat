"""Test the update method for a WD5-series thermostat."""

import json
from datetime import datetime, timedelta

import pytest
import pytz
from freezegun import freeze_time
from ojmicroline_thermostat.const import (
    COMFORT_DURATION,
    REGULATION_BOOST,
    REGULATION_COMFORT,
    REGULATION_MANUAL,
)
from ojmicroline_thermostat.models import Thermostat
from ojmicroline_thermostat.wd5 import WD5API

from . import load_fixtures


def datetime_now() -> datetime:
    """Return the date in local timezone because freezegun doesn't respect UTC.

    @link https://github.com/spulec/freezegun/issues/89
    """
    return datetime.utcnow().replace(tzinfo=pytz.utc).astimezone()  # noqa: DTZ003


@pytest.mark.asyncio
@freeze_time("2023-01-01 11:30:35")
async def test_update_regulation_mode_comfort() -> None:
    """Test that the regulation mode can be set to comfort.

    Make sure the end times are set correctly.
    """
    data = load_fixtures("wd5_thermostat.json")
    thermostat = Thermostat.from_wd5_json(json.loads(data))

    # Check the current times.
    assert _fmt(thermostat.comfort_end_time) == "2022-12-28T14:58:34"
    assert _fmt(thermostat.boost_end_time) == "2023-01-02T13:55:01"
    assert _fmt(thermostat.vacation_begin_time) == "2022-12-20T23:00:00"
    assert _fmt(thermostat.vacation_end_time) == "2022-12-21T23:00:00"

    # Check the current temperature for comfort.
    assert thermostat.comfort_temperature == 2000

    request = WD5API(
        api_key="v3ry-s3cr3t",
        customer_id=42,
        username="username",
        password="password",
    )
    result = request.update_regulation_mode_body(
        thermostat=thermostat,
        regulation_mode=REGULATION_COMFORT,
        temperature=None,
        duration=COMFORT_DURATION,
    )

    # Assert comfort end time is the current date + COMFORT_DURATION minutes.
    assert result["SetGroup"]["ComfortEndTime"] == _fmt(
        datetime_now() + timedelta(minutes=COMFORT_DURATION)
    )

    # Assert rest is the same.
    assert result["SetGroup"]["BoostEndTime"] == "2023-01-02T13:55:01"
    assert result["SetGroup"]["VacationBeginDay"] == "2022-12-20T23:00:00"
    assert result["SetGroup"]["VacationEndDay"] == "2022-12-21T23:00:00"

    # Assert the temperature is the same.
    assert result["SetGroup"]["ComfortSetpoint"] == 2000


@pytest.mark.asyncio
@freeze_time("2023-01-01 11:30:35")
async def test_update_regulation_mode_comfort_with_temp_and_duration() -> None:
    """Test that the regulation mode can be set to comfort.

    Make sure the end times are set correctly.
    """
    data = load_fixtures("wd5_thermostat.json")
    thermostat = Thermostat.from_wd5_json(json.loads(data))

    # Check the current times.
    assert _fmt(thermostat.comfort_end_time) == "2022-12-28T14:58:34"
    assert _fmt(thermostat.boost_end_time) == "2023-01-02T13:55:01"
    assert _fmt(thermostat.vacation_begin_time) == "2022-12-20T23:00:00"
    assert _fmt(thermostat.vacation_end_time) == "2022-12-21T23:00:00"

    # Check the current temperature for comfort.
    assert thermostat.comfort_temperature == 2000

    request = WD5API(
        api_key="v3ry-s3cr3t",
        customer_id=42,
        username="username",
        password="password",
    )
    result = request.update_regulation_mode_body(
        thermostat=thermostat,
        regulation_mode=REGULATION_COMFORT,
        temperature=2350,
        duration=360,
    )

    # Assert comfort end time is the current date + 360 minutes.
    assert result["SetGroup"]["ComfortEndTime"] == _fmt(
        datetime_now() + timedelta(minutes=360)
    )

    # Assert rest is the same.
    assert result["SetGroup"]["BoostEndTime"] == "2023-01-02T13:55:01"
    assert result["SetGroup"]["VacationBeginDay"] == "2022-12-20T23:00:00"
    assert result["SetGroup"]["VacationEndDay"] == "2022-12-21T23:00:00"

    # Assert the temperature is the same.
    assert result["SetGroup"]["ComfortSetpoint"] == 2350


@pytest.mark.asyncio
@freeze_time("2023-01-01 11:30:35")
async def test_update_regulation_mode_boost() -> None:
    """Test that the regulation mode can be set to boost.

    Make sure the end times are set correctly.
    """
    data = load_fixtures("wd5_thermostat.json")
    thermostat = Thermostat.from_wd5_json(json.loads(data))

    # Check the current times.
    assert _fmt(thermostat.comfort_end_time) == "2022-12-28T14:58:34"
    assert _fmt(thermostat.boost_end_time) == "2023-01-02T13:55:01"
    assert _fmt(thermostat.vacation_begin_time) == "2022-12-20T23:00:00"
    assert _fmt(thermostat.vacation_end_time) == "2022-12-21T23:00:00"

    request = WD5API(
        api_key="v3ry-s3cr3t",
        customer_id=42,
        username="username",
        password="password",
    )
    result = request.update_regulation_mode_body(
        thermostat=thermostat,
        regulation_mode=REGULATION_BOOST,
        temperature=None,
        duration=COMFORT_DURATION,
    )

    # Assert comfort end time is the current date + 1 hours.
    assert result["SetGroup"]["BoostEndTime"] == _fmt(
        datetime_now() + timedelta(hours=1)
    )

    # Assert rest stayed the same.
    assert result["SetGroup"]["ComfortEndTime"] == "2022-12-28T14:58:34"
    assert result["SetGroup"]["VacationBeginDay"] == "2022-12-20T23:00:00"
    assert result["SetGroup"]["VacationEndDay"] == "2022-12-21T23:00:00"


@pytest.mark.asyncio
@freeze_time("2023-01-01 11:30:35")
async def test_update_regulation_mode_with_temp() -> None:
    """Test that the regulation mode can be set to manual mode.

    Make sure the end times are not adjusted.
    """
    data = load_fixtures("wd5_thermostat.json")
    thermostat = Thermostat.from_wd5_json(json.loads(data))

    # Check the current times.
    assert _fmt(thermostat.comfort_end_time) == "2022-12-28T14:58:34"
    assert _fmt(thermostat.boost_end_time) == "2023-01-02T13:55:01"
    assert _fmt(thermostat.vacation_begin_time) == "2022-12-20T23:00:00"
    assert _fmt(thermostat.vacation_end_time) == "2022-12-21T23:00:00"

    # Check the current temperature for manual.
    assert thermostat.manual_temperature == 2350

    request = WD5API(
        api_key="v3ry-s3cr3t",
        customer_id=42,
        username="username",
        password="password",
    )
    result = request.update_regulation_mode_body(
        thermostat=thermostat,
        regulation_mode=REGULATION_MANUAL,
        temperature=3000,
        duration=COMFORT_DURATION,
    )

    # Assert the dates are unchanged.
    assert result["SetGroup"]["BoostEndTime"] == "2023-01-02T13:55:01"
    assert result["SetGroup"]["ComfortEndTime"] == "2022-12-28T14:58:34"
    assert result["SetGroup"]["VacationBeginDay"] == "2022-12-20T23:00:00"
    assert result["SetGroup"]["VacationEndDay"] == "2022-12-21T23:00:00"

    # Assert the temperature is updated.
    assert result["SetGroup"]["ManualModeSetpoint"] == 3000


def _fmt(date: datetime | None) -> str:
    return "None" if date is None else date.strftime("%Y-%m-%dT%H:%M:%S")
