"""Test the update method for a WD5-series thermostat."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from freezegun import freeze_time
from ojmicroline_thermostat.const import (
    COMFORT_DURATION,
    REGULATION_COMFORT,
    REGULATION_MANUAL,
    WG4_DATETIME_FORMAT,
)
from ojmicroline_thermostat.models import Thermostat
from ojmicroline_thermostat.wg4 import WG4API

from . import load_fixtures


@pytest.mark.asyncio
@freeze_time("2024-01-25 12:00:00")
async def test_update_regulation_mode_comfort() -> None:
    """Test that the regulation mode can be set to comfort.

    Make sure the end times are set correctly.
    """
    data = load_fixtures("wg4_thermostat.json")
    thermostat = Thermostat.from_wg4_json(json.loads(data))

    # Check the current times.
    assert thermostat.comfort_end_time == datetime(2024, 1, 24, 5, tzinfo=UTC)

    # Check the current temperature for comfort.
    assert thermostat.comfort_temperature == 2000

    request = WG4API(
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
    assert datetime.strptime(  # noqa: DTZ007
        result["ComfortEndTime"], WG4_DATETIME_FORMAT
    ) == datetime.now(tz=UTC) + timedelta(minutes=COMFORT_DURATION)


@pytest.mark.asyncio
@freeze_time("2024-01-25 12:00:00")
async def test_update_regulation_mode_comfort_with_temp_and_duration() -> None:
    """Test that the regulation mode can be set to comfort.

    Make sure the end times are set correctly.
    """
    data = load_fixtures("wg4_thermostat.json")
    thermostat = Thermostat.from_wg4_json(json.loads(data))

    # Check the current times.
    assert thermostat.comfort_end_time == datetime(2024, 1, 24, 5, tzinfo=UTC)

    # Check the current temperature for comfort.
    assert thermostat.comfort_temperature == 2000

    request = WG4API(
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
    assert datetime.strptime(  # noqa: DTZ007
        result["ComfortEndTime"], WG4_DATETIME_FORMAT
    ) == datetime.now(tz=UTC) + timedelta(minutes=360)

    # Assert the temperature is the same.
    assert result["ComfortTemperature"] == 2350


@pytest.mark.asyncio
@freeze_time("2024-01-25 12:00:00")
async def test_update_regulation_mode_with_temp() -> None:
    """Test that the regulation mode can be set to manual mode.

    Make sure the end times are not adjusted.
    """
    data = load_fixtures("wg4_thermostat.json")
    thermostat = Thermostat.from_wg4_json(json.loads(data))

    # Check the current times.
    assert thermostat.comfort_end_time == datetime(2024, 1, 24, 5, tzinfo=UTC)

    # Check the current temperature for manual.
    assert thermostat.manual_temperature == 2600

    request = WG4API(
        username="username",
        password="password",
    )
    result = request.update_regulation_mode_body(
        thermostat=thermostat,
        regulation_mode=REGULATION_MANUAL,
        temperature=3000,
        duration=COMFORT_DURATION,
    )

    # Assert the temperature is updated.
    assert result["ManualTemperature"] == 3000
