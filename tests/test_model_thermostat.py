"""Test the Thermostat model for WD5-series APIs."""

import json
from datetime import datetime, timedelta, timezone

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
async def test_thermostat_from_json_wd5() -> None:
    """Make sure the data is accepted by the from_wd5_json method."""
    data = load_fixtures("wd5_thermostat.json")
    thermostat = Thermostat.from_wd5_json(json.loads(data))

    for field in REQUIRED_FIELDS + WD5_ONLY_FIELDS:
        assert getattr(thermostat, field) is not None, (
            f"Expected {field} to be non-null"
        )
    for field in WG4_ONLY_FIELDS:
        assert getattr(thermostat, field) is None, f"Expected {field} to be null"

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
    assert thermostat.supported_regulation_modes == [
        REGULATION_SCHEDULE,
        REGULATION_COMFORT,
        REGULATION_MANUAL,
        REGULATION_VACATION,
        REGULATION_FROST_PROTECTION,
        REGULATION_BOOST,
        REGULATION_ECO,
    ]
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
    assert isinstance(thermostat.schedule, object)
    assert thermostat.comfort_temperature == 2000
    assert thermostat.manual_temperature == 2350
    assert thermostat.vacation_temperature == 500
    assert thermostat.frost_protection_temperature == 500
    assert thermostat.boost_temperature == 4000


@pytest.mark.asyncio
@freeze_time("2023-01-01 11:30:35")
async def test_thermostat_get_current_temperature() -> None:
    """Make sure the right temperature is returned for every sensor mode."""
    fixture = load_fixtures("wd5_thermostat.json")
    data = json.loads(fixture)
    data["FloorTemperature"] = 4000
    data["RoomTemperature"] = 2000

    data["SensorAppl"] = SENSOR_FLOOR
    thermostat = Thermostat.from_wd5_json(data)
    assert thermostat.get_current_temperature() == 4000

    data["SensorAppl"] = SENSOR_ROOM
    thermostat = Thermostat.from_wd5_json(data)
    assert thermostat.get_current_temperature() == 2000

    data["SensorAppl"] = SENSOR_ROOM_FLOOR
    thermostat = Thermostat.from_wd5_json(data)
    assert thermostat.get_current_temperature() == 3000

    data["SensorAppl"] = 999
    thermostat = Thermostat.from_wd5_json(data)
    assert thermostat.get_current_temperature() == 0


@pytest.mark.asyncio
@freeze_time("2023-01-01 11:30:35")
async def test_thermostat_get_target_temperature() -> None:
    """Test the right temperature is returned for all regulation modes."""
    fixture = load_fixtures("wd5_thermostat.json")
    data = json.loads(fixture)
    data["Schedule"]["Days"][6]["Events"][1]["Temperature"] = 400
    data["ComfortSetpoint"] = 2
    data["ManualModeSetpoint"] = 3
    data["VacationTemperature"] = 4
    data["FrostProtectionTemperature"] = 5
    data["MaxSetpoint"] = 6
    data["Schedule"]["Days"][6]["Events"][0]["Temperature"] = 300

    data["RegulationMode"] = REGULATION_SCHEDULE
    thermostat = Thermostat.from_wd5_json(data)
    assert thermostat.get_target_temperature() == 400

    data["RegulationMode"] = REGULATION_COMFORT
    thermostat = Thermostat.from_wd5_json(data)
    assert thermostat.get_target_temperature() == 2

    data["RegulationMode"] = REGULATION_MANUAL
    thermostat = Thermostat.from_wd5_json(data)
    assert thermostat.get_target_temperature() == 3

    data["RegulationMode"] = REGULATION_VACATION
    thermostat = Thermostat.from_wd5_json(data)
    assert thermostat.get_target_temperature() == 4

    data["RegulationMode"] = REGULATION_FROST_PROTECTION
    thermostat = Thermostat.from_wd5_json(data)
    assert thermostat.get_target_temperature() == 5

    data["RegulationMode"] = REGULATION_BOOST
    thermostat = Thermostat.from_wd5_json(data)
    assert thermostat.get_target_temperature() == 6

    data["RegulationMode"] = REGULATION_ECO
    thermostat = Thermostat.from_wd5_json(data)
    assert thermostat.get_target_temperature() == 300

    data["RegulationMode"] = 42
    thermostat = Thermostat.from_wd5_json(data)
    assert thermostat.get_target_temperature() == 0


@pytest.mark.asyncio
async def test_thermostat_from_json_wg4() -> None:
    """Make sure the data is accepted by the from_wg4_json method."""
    data = load_fixtures("wg4_thermostat.json")
    thermostat = Thermostat.from_wg4_json(json.loads(data))

    for field in REQUIRED_FIELDS + WG4_ONLY_FIELDS:
        assert getattr(thermostat, field) is not None, (
            f"Expected {field} to be non-null"
        )
    for field in WD5_ONLY_FIELDS:
        assert getattr(thermostat, field) is None, f"Expected {field} to be null"

    assert thermostat.model == "UWG4"
    assert thermostat.serial_number == "42424242"
    assert thermostat.software_version == "1012M203"
    assert thermostat.zone_name == ""
    assert thermostat.zone_id == -1
    assert thermostat.name == "RoomName"
    assert thermostat.online is True
    assert thermostat.heating is False
    assert thermostat.regulation_mode == 3
    assert thermostat.supported_regulation_modes == [
        REGULATION_SCHEDULE,
        REGULATION_COMFORT,
        REGULATION_MANUAL,
        REGULATION_VACATION,
    ]
    assert thermostat.last_primary_mode_is_auto is False
    assert thermostat.min_temperature == 500
    assert thermostat.max_temperature == 4000
    assert thermostat.temperature == 2200
    assert thermostat.set_point_temperature == 2600
    assert isinstance(thermostat.comfort_end_time, datetime)
    assert thermostat.comfort_temperature == 2000
    assert thermostat.manual_temperature == 2600
    assert thermostat.vacation_mode is True
    assert thermostat.vacation_temperature == 600
    tzinfo = timezone(-timedelta(hours=6))
    assert thermostat.vacation_begin_time == datetime(2024, 2, 12, tzinfo=tzinfo)
    assert thermostat.vacation_end_time == datetime(2024, 2, 16, tzinfo=tzinfo)

    # Test the getter methods:
    assert thermostat.get_current_temperature() == 2200
    assert thermostat.get_target_temperature() == 2600


@pytest.mark.asyncio
async def test_thermostat_from_json_wd5_timezone_negative() -> None:
    """Make sure a negative timezone is set when TimeZone is negative."""
    data = json.loads(load_fixtures("wd5_thermostat.json"))
    data["TimeZone"] = -7200

    thermostat = Thermostat.from_wd5_json(data)
    assert thermostat.comfort_end_time.strftime("%z") == "-0200"


@pytest.mark.asyncio
async def test_thermostat_from_json_wg5() -> None:
    """Make sure the data is accepted by the from_wg5_json method."""
    data = json.loads(load_fixtures("wg5_thermostat_control.json"))
    thermostat = Thermostat.from_wg5_json(
        data["data"],
        building_id="57dc7778-4ed3-4382-9e89-5cf2e0bc0f8a",
        zone_name="Default",
    )

    for field in WG5_ONLY_FIELDS:
        assert getattr(thermostat, field) is not None, (
            f"Expected {field} to be non-null"
        )
    for field in WD5_EXCLUSIVE_FIELDS:
        assert getattr(thermostat, field) is None, f"Expected {field} to be null"

    assert thermostat.model == "WG5"
    assert thermostat.serial_number == "2cb3e6c5-8cf8-4e7e-943a-42618c34a506"
    assert thermostat.software_version == ""
    assert thermostat.zone_name == "Default"
    assert thermostat.zone_id == 0
    assert thermostat.zone_uuid == "ae85d8fc-1b4a-445a-bdf1-05f970fc3da4"
    assert thermostat.building_id == "57dc7778-4ed3-4382-9e89-5cf2e0bc0f8a"
    assert thermostat.name == "Bathroom"
    assert thermostat.online is True
    assert thermostat.heating is True
    assert thermostat.regulation_mode == REGULATION_MANUAL
    assert thermostat.supported_regulation_modes == [
        REGULATION_SCHEDULE,
        REGULATION_MANUAL,
        REGULATION_COMFORT,
        REGULATION_VACATION,
        REGULATION_FROST_PROTECTION,
    ]
    assert thermostat.min_temperature == 500
    assert thermostat.max_temperature == 4000
    assert thermostat.temperature == 1900
    assert thermostat.set_point_temperature == 2778
    assert thermostat.manual_temperature == 2778
    assert thermostat.comfort_temperature == 2778
    assert thermostat.frost_protection_temperature == 500
    assert thermostat.sensor_mode == SENSOR_FLOOR
    assert thermostat.vacation_mode is False
    assert thermostat.is_in_standby is False
    assert thermostat.open_window_detection is False
    assert thermostat.schedule_id == "f9aac20b-4e45-415b-9f56-e53014ee8639"
    assert thermostat.schedule_name == "Default Schedule"

    # Test the getter methods:
    assert thermostat.get_current_temperature() == 1900
    assert thermostat.get_target_temperature() == 2778


@pytest.mark.asyncio
async def test_thermostat_from_json_wg5_standby() -> None:
    """Make sure standby mode maps to REGULATION_FROST_PROTECTION."""
    data = json.loads(load_fixtures("wg5_thermostat_control.json"))
    data["data"]["mode"]["isInStandby"] = True

    thermostat = Thermostat.from_wg5_json(
        data["data"],
        building_id="57dc7778-4ed3-4382-9e89-5cf2e0bc0f8a",
        zone_name="Default",
    )

    assert thermostat.regulation_mode == REGULATION_FROST_PROTECTION
    assert thermostat.is_in_standby is True


@pytest.mark.asyncio
async def test_thermostat_from_json_wg5_away() -> None:
    """Make sure away_active maps to REGULATION_VACATION."""
    data = json.loads(load_fixtures("wg5_thermostat_control.json"))

    thermostat = Thermostat.from_wg5_json(
        data["data"],
        building_id="57dc7778-4ed3-4382-9e89-5cf2e0bc0f8a",
        zone_name="Default",
        away_active=True,
    )

    assert thermostat.regulation_mode == REGULATION_VACATION
    assert thermostat.vacation_mode is True


@pytest.mark.asyncio
async def test_thermostat_from_json_wg5_schedule() -> None:
    """Make sure fallbackMode Auto maps to REGULATION_SCHEDULE."""
    data = json.loads(load_fixtures("wg5_thermostat_control.json"))
    data["data"]["mode"]["fallbackMode"] = "Auto"

    thermostat = Thermostat.from_wg5_json(
        data["data"],
        building_id="57dc7778-4ed3-4382-9e89-5cf2e0bc0f8a",
        zone_name="Default",
    )

    assert thermostat.regulation_mode == REGULATION_SCHEDULE
    assert thermostat.last_primary_mode_is_auto is True
    assert thermostat.is_in_standby is False


@pytest.mark.asyncio
async def test_thermostat_get_current_energy_with_data() -> None:
    """Test get_current_energy returns the first element when energy is set."""
    data = json.loads(load_fixtures("wd5_thermostat.json"))
    thermostat = Thermostat.from_wd5_json(data)
    thermostat.energy = [3.5, 2.1, 1.0]

    assert thermostat.get_current_energy() == 3.5

    thermostat.energy = None
    assert thermostat.get_current_energy() == 0.0


REQUIRED_FIELDS = [
    "model",
    "serial_number",
    "software_version",
    "zone_name",
    "zone_id",
    "name",
    "online",
    "heating",
    "regulation_mode",
    "supported_regulation_modes",
    "min_temperature",
    "max_temperature",
    "comfort_temperature",
    "manual_temperature",
    "comfort_end_time",
    "last_primary_mode_is_auto",
    "vacation_mode",
    "vacation_begin_time",
    "vacation_end_time",
    "vacation_temperature",
]

WG4_ONLY_FIELDS = [
    "temperature",
    "set_point_temperature",
]

WD5_ONLY_FIELDS = [
    "thermostat_id",
    "schedule",
    "adaptive_mode",
    "open_window_detection",
    "daylight_saving_active",
    "sensor_mode",
    "temperature_floor",
    "temperature_room",
    "boost_end_time",
    "frost_protection_temperature",
    "boost_temperature",
]

WD5_EXCLUSIVE_FIELDS = [
    "thermostat_id",
    "schedule",
    "adaptive_mode",
    "daylight_saving_active",
    "temperature_floor",
    "temperature_room",
    "boost_end_time",
    "boost_temperature",
]

WG5_ONLY_FIELDS = [
    "building_id",
    "zone_uuid",
    "is_in_standby",
    "schedule_id",
    "schedule_name",
    "temperature",
    "set_point_temperature",
    "sensor_mode",
    "open_window_detection",
    "frost_protection_temperature",
    "vacation_mode",
]
