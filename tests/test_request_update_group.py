"""Test the models."""
import pytest
import json
from datetime import datetime, timedelta
from freezegun import freeze_time

from . import load_fixtures

from ojmicroline_thermostat.const import (
    REGULATION_COMFORT,
    REGULATION_MANUAL,
    REGULATION_BOOST,
)

from ojmicroline_thermostat.requests import UpdateGroupRequest
from ojmicroline_thermostat.models import Thermostat


@pytest.mark.asyncio
async def test_update_group_request_init() -> None:
    """Make sure the data is accepted by the init method."""
    data = load_fixtures("single_thermostat.json")
    thermostat = Thermostat.from_json(json.loads(data))

    request = UpdateGroupRequest(resource=thermostat, api_key="v3ry-s3cr3t")

    assert isinstance(request.resource, Thermostat)
    assert request.api_key is not None


@pytest.mark.asyncio
@freeze_time("2023-01-01 11:30:35")
async def test_update_regulation_mode_comfort() -> None:
    """
    Test that the regulation mode can be set to comfort.

    Make sure the end times are set correctly.
    """
    data = load_fixtures("single_thermostat.json")
    thermostat = Thermostat.from_json(json.loads(data))
    dateformat = "%Y-%m-%dT%H:%M:%S"

    """Check the current times."""
    assert thermostat.comfort_end_time.strftime(dateformat) == "2022-12-28T15:58:34"
    assert thermostat.boost_end_time.strftime(dateformat) == "2023-01-02T14:55:01"
    assert thermostat.vacation_begin_time.strftime(dateformat) == "2022-12-21T00:00:00"
    assert thermostat.vacation_end_time.strftime(dateformat) == "2022-12-22T00:00:00"

    """Check the current temperature for comfort."""
    assert thermostat.temperatures[REGULATION_COMFORT] == 2000

    request = UpdateGroupRequest(resource=thermostat, api_key="v3ry-s3cr3t")
    result = request.update_regulation_mode(REGULATION_COMFORT)

    """Assert comfort end time is the current date + 4 hours"""
    assert result["SetGroup"]["ComfortEndTime"] == (
        datetime.now() + timedelta(hours=4)
    ).strftime(dateformat)

    """Assert rest is the same."""
    assert result["SetGroup"]["BoostEndTime"] == "2023-01-02T14:55:01"
    assert result["SetGroup"]["VacationBeginDay"] == "2022-12-21T00:00:00"
    assert result["SetGroup"]["VacationEndDay"] == "2022-12-22T00:00:00"

    """Assert the temperature is the same."""
    assert result["SetGroup"]["ComfortSetpoint"] == 2000


@pytest.mark.asyncio
@freeze_time("2023-01-01 11:30:35")
async def test_update_regulation_mode_boost() -> None:
    """
    Test that the regulation mode can be set to boost.

    Make sure the end times are set correctly.
    """
    data = load_fixtures("single_thermostat.json")
    thermostat = Thermostat.from_json(json.loads(data))
    dateformat = "%Y-%m-%dT%H:%M:%S"

    """Check the current times."""
    assert thermostat.comfort_end_time.strftime(dateformat) == "2022-12-28T15:58:34"
    assert thermostat.boost_end_time.strftime(dateformat) == "2023-01-02T14:55:01"
    assert thermostat.vacation_begin_time.strftime(dateformat) == "2022-12-21T00:00:00"
    assert thermostat.vacation_end_time.strftime(dateformat) == "2022-12-22T00:00:00"

    request = UpdateGroupRequest(resource=thermostat, api_key="v3ry-s3cr3t")
    result = request.update_regulation_mode(REGULATION_BOOST)

    """Assert comfort end time is the current date + 1 hours"""
    assert result["SetGroup"]["BoostEndTime"] == (
        datetime.now() + timedelta(hours=1)
    ).strftime(dateformat)

    """Assert rest is the same."""
    assert result["SetGroup"]["ComfortEndTime"] == "2022-12-28T15:58:34"
    assert result["SetGroup"]["VacationBeginDay"] == "2022-12-21T00:00:00"
    assert result["SetGroup"]["VacationEndDay"] == "2022-12-22T00:00:00"


@pytest.mark.asyncio
@freeze_time("2023-01-01 11:30:35")
async def test_update_regulation_mode_with_temp() -> None:
    """
    Test that the regulation mode can be set to manual mode.

    Make sure the end times are not adjusted.
    """

    data = load_fixtures("single_thermostat.json")
    thermostat = Thermostat.from_json(json.loads(data))
    dateformat = "%Y-%m-%dT%H:%M:%S"

    """Check the current times."""
    assert thermostat.comfort_end_time.strftime(dateformat) == "2022-12-28T15:58:34"
    assert thermostat.boost_end_time.strftime(dateformat) == "2023-01-02T14:55:01"
    assert thermostat.vacation_begin_time.strftime(dateformat) == "2022-12-21T00:00:00"
    assert thermostat.vacation_end_time.strftime(dateformat) == "2022-12-22T00:00:00"

    """Check the current temperature for comfort."""
    assert thermostat.temperatures[REGULATION_MANUAL] == 2350

    request = UpdateGroupRequest(resource=thermostat, api_key="v3ry-s3cr3t")
    result = request.update_regulation_mode(REGULATION_MANUAL, 3000)

    """Assert the dates are unchanged"""
    assert result["SetGroup"]["BoostEndTime"] == "2023-01-02T14:55:01"
    assert result["SetGroup"]["ComfortEndTime"] == "2022-12-28T15:58:34"
    assert result["SetGroup"]["VacationBeginDay"] == "2022-12-21T00:00:00"
    assert result["SetGroup"]["VacationEndDay"] == "2022-12-22T00:00:00"

    """Assert the temperature is updated."""
    assert result["SetGroup"]["ManualModeSetpoint"] == 3000
