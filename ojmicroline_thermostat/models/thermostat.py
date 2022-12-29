"""Thermostat model for OJ Microline Thermostat."""
from __future__ import annotations
from .schedule import Schedule
from datetime import datetime
from time import strftime, gmtime

from math import ceil
from dataclasses import dataclass
from typing import Any

from ..const import (
    REGULATION_SCHEDULE,
    REGULATION_ECO,
    REGULATION_MANUAL,
    REGULATION_FROST_PROTECTION,
    REGULATION_BOOST,
    REGULATION_COMFORT,
    REGULATION_VACATION,
    SENSOR_FLOOR,
    SENSOR_ROOM,
    SENSOR_ROOM_FLOOR,
)


@ dataclass
class Thermostat:
    """Object representing a Thermostat model response from the API."""

    thermostat_id: int
    model: str
    serial_number: str
    software_version: str
    zone_name: str
    zone_id: int
    name: str
    online: bool
    heating: bool
    regulation_mode: int
    sensor_mode: int
    adaptive_mode: bool
    open_window_detection: bool
    last_primary_mode_is_auto: bool
    daylight_saving_active: bool
    vacation_enabled: bool
    temperature_floor: int
    temperature_room: int
    min_temperature: int
    max_temperature: int
    temperatures: dict[int, int]
    dates: dict[str, datetime]
    offset: int
    schedule: dict[Any]

    @ classmethod
    def from_json(cls, data: dict[Any]) -> Thermostat:
        """
        Return a new Thermostat instance based on the given JSON.

        Args:
            data: The JSON data from the API.

        Returns:
            A Thermostat Object.
        """

        schedule = Schedule.from_json(data["Schedule"])
        time_zone = data["TimeZone"]
        regulation_mode = data["RegulationMode"]

        return cls(
            thermostat_id=data["Id"],
            model="OWD5",
            serial_number=data["SerialNumber"],
            software_version=data["SWversion"],
            zone_name=data["GroupName"],
            zone_id=data["GroupId"],
            name=data["ThermostatName"],
            online=data["Online"],
            heating=data["Heating"],
            regulation_mode=regulation_mode,
            sensor_mode=data["SensorAppl"],
            adaptive_mode=data["AdaptiveMode"],
            open_window_detection=data["OpenWindow"],
            last_primary_mode_is_auto=data["LastPrimaryModeIsAuto"],
            daylight_saving_active=data["DaylightSavingActive"],
            vacation_enabled=data["VacationEnabled"],
            temperature_floor=data["FloorTemperature"],
            temperature_room=data["RoomTemperature"],
            min_temperature=data["MinSetpoint"],
            max_temperature=data["MaxSetpoint"],
            temperatures={
                REGULATION_SCHEDULE: schedule.get_active_temperature(),
                REGULATION_COMFORT: data["ComfortSetpoint"],
                REGULATION_MANUAL: data["ManualModeSetpoint"],
                REGULATION_VACATION: data["VacationTemperature"],
                REGULATION_FROST_PROTECTION: data["FrostProtectionTemperature"],
                REGULATION_BOOST: data["MaxSetpoint"],
                REGULATION_ECO: schedule.get_lowest_temperature(),
            },
            dates={
                "boost_end_time": parse_date(data["BoostEndTime"], time_zone),
                "comfort_end_time": parse_date(data["ComfortEndTime"], time_zone),
                "vacation_begin_time": parse_date(data["VacationBeginDay"], time_zone),
                "vacation_end_time": parse_date(data["VacationEndDay"], time_zone),
            },
            offset=time_zone,
            schedule=data["Schedule"],
        )

    def get_target_temperature(cls) -> int:
        """
        Return the target temperature for the thermostat.

        Returns:
            The current temperature.
        """
        return cls.temperatures[cls.regulation_mode]

    def get_current_temperature(cls) -> int:
        """
        Return the current temperature for the thermostat.

        Get the current temperature based on the sensor type. If it is
        set to room / floor, then the average is used.

        Returns:
            The current temperature.
        """
        if cls.sensor_mode == SENSOR_ROOM:
            return cls.temperature_room
        elif cls.sensor_mode == SENSOR_FLOOR:
            return cls.temperature_floor
        elif cls.sensor_mode == SENSOR_ROOM_FLOOR:
            return ceil((cls.temperature_floor + cls.temperature_room) / 2)

        return 0


def parse_date(value: str, offset: int) -> datetime:
    """
    Parse a given value and offset into a datetime object.

    Args:
        value: The date time string.
        offset: The time zone offset in seconds.

    Returns:
        The parsed datetime.
    """
    timezone = strftime("%H:%M", gmtime(abs(offset)))
    separator = "+" if offset >= 0 else "-"

    return datetime.strptime(f"{value}{separator}{timezone}", "%Y-%m-%dT%H:%M:%S%z")
