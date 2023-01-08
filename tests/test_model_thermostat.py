"""Test the models."""
import json
from datetime import datetime

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
    SENSOR_FLOOR,
    SENSOR_ROOM,
    SENSOR_ROOM_FLOOR,
)
from ojmicroline_thermostat.models import Thermostat

from . import load_fixtures


@pytest.mark.asyncio
@freeze_time("2023-01-01 11:30:35")
async def test_thermostat_from_json() -> None:
    """Make sure the data is accepted by the from_json method."""
    data = load_fixtures("single_thermostat.json")
    thermostat = Thermostat.from_json(json.loads(data))

    fields = [
        "thermostat_id",
        "model",
        "serial_number",
        "software_version",
        "zone_name",
        "zone_id",
        "name",
        "online",
        "heating",
        "regulation_mode",
        "sensor_mode",
        "adaptive_mode",
        "open_window_detection",
        "last_primary_mode_is_auto",
        "daylight_saving_active",
        "vacation_mode",
        "temperature_floor",
        "temperature_room",
        "min_temperature",
        "max_temperature",
        "temperatures",
        "boost_end_time",
        "comfort_end_time",
        "vacation_begin_time",
        "vacation_end_time",
        "offset",
        "schedule",
    ]

    for field in fields:
        assert getattr(thermostat, field) is not None

    assert thermostat.thermostat_id == 117006
    assert thermostat.model == "OWD5"
    assert thermostat.serial_number == "800747"
    assert thermostat.software_version == "1013W213"
    assert thermostat.zone_name == "Woonkamer"
    assert thermostat.zone_id == 50369
    assert thermostat.name == "Woonkamer"
    assert thermostat.online is True
    assert thermostat.heating is False
    assert thermostat.regulation_mode == 1
    assert thermostat.sensor_mode == 3
    assert thermostat.adaptive_mode is True
    assert thermostat.open_window_detection is False
    assert thermostat.last_primary_mode_is_auto is True
    assert thermostat.daylight_saving_active is False
    assert thermostat.vacation_mode is False
    assert thermostat.temperature_floor == 1968
    assert thermostat.temperature_room == 1820
    assert thermostat.min_temperature == 500
    assert thermostat.max_temperature == 4000
    assert isinstance(thermostat.boost_end_time, datetime)
    assert isinstance(thermostat.comfort_end_time, datetime)
    assert isinstance(thermostat.vacation_begin_time, datetime)
    assert isinstance(thermostat.vacation_end_time, datetime)
    assert thermostat.offset == 3600
    assert isinstance(thermostat.schedule, object)
    assert thermostat.temperatures[1] == 1800
    assert thermostat.temperatures[2] == 2000
    assert thermostat.temperatures[3] == 2350
    assert thermostat.temperatures[4] == 500
    assert thermostat.temperatures[6] == 500
    assert thermostat.temperatures[8] == 4000
    assert thermostat.temperatures[9] == 1500


@pytest.mark.asyncio
@freeze_time("2023-01-01 11:30:35")
async def test_thermostat_get_current_temperature() -> None:
    """Make sure the right temperature is returned for every sensor mode."""
    fixture = load_fixtures("single_thermostat.json")
    data = json.loads(fixture)
    data["FloorTemperature"] = 4000
    data["RoomTemperature"] = 2000

    data["SensorAppl"] = SENSOR_FLOOR
    thermostat = Thermostat.from_json(data)
    assert thermostat.get_current_temperature() == 4000

    data["SensorAppl"] = SENSOR_ROOM
    thermostat = Thermostat.from_json(data)
    assert thermostat.get_current_temperature() == 2000

    data["SensorAppl"] = SENSOR_ROOM_FLOOR
    thermostat = Thermostat.from_json(data)
    assert thermostat.get_current_temperature() == 3000

    data["SensorAppl"] = 999
    thermostat = Thermostat.from_json(data)
    assert thermostat.get_current_temperature() == 0


@pytest.mark.asyncio
@freeze_time("2023-01-01 11:30:35")
async def test_thermostat_get_target_temperature() -> None:
    """Test the right temperatuer is returned for all regulation modes."""
    fixture = load_fixtures("single_thermostat.json")
    data = json.loads(fixture)
    data["Schedule"]["Days"][6]["Events"][1]["Temperature"] = 400
    data["ComfortSetpoint"] = 2
    data["ManualModeSetpoint"] = 3
    data["VacationTemperature"] = 4
    data["FrostProtectionTemperature"] = 5
    data["MaxSetpoint"] = 6
    data["Schedule"]["Days"][6]["Events"][0]["Temperature"] = 300

    data["RegulationMode"] = REGULATION_SCHEDULE
    thermostat = Thermostat.from_json(data)
    assert thermostat.get_target_temperature() == 400

    data["RegulationMode"] = REGULATION_COMFORT
    thermostat = Thermostat.from_json(data)
    assert thermostat.get_target_temperature() == 2

    data["RegulationMode"] = REGULATION_MANUAL
    thermostat = Thermostat.from_json(data)
    assert thermostat.get_target_temperature() == 3

    data["RegulationMode"] = REGULATION_VACATION
    thermostat = Thermostat.from_json(data)
    assert thermostat.get_target_temperature() == 4

    data["RegulationMode"] = REGULATION_FROST_PROTECTION
    thermostat = Thermostat.from_json(data)
    assert thermostat.get_target_temperature() == 5

    data["RegulationMode"] = REGULATION_BOOST
    thermostat = Thermostat.from_json(data)
    assert thermostat.get_target_temperature() == 6

    data["RegulationMode"] = REGULATION_ECO
    thermostat = Thermostat.from_json(data)
    assert thermostat.get_target_temperature() == 300
