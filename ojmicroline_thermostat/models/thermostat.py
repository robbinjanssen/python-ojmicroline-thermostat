# ruff: noqa: PLR0911
# pylint: disable=too-many-instance-attributes,too-many-return-statements
"""Thermostat model for OJ Microline Thermostat."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from time import gmtime, strftime
from typing import Any

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
    WG4_DATETIME_FORMAT,
    WG5_SENSOR_MAP,
)

from .schedule import Schedule


@dataclass
class Thermostat:
    """Object representing a Thermostat model response from the API."""

    model: str
    serial_number: str
    software_version: str
    zone_name: str
    zone_id: int
    name: str
    online: bool
    heating: bool
    regulation_mode: int
    supported_regulation_modes: list[int]
    min_temperature: int
    max_temperature: int
    manual_temperature: int
    comfort_temperature: int
    comfort_end_time: datetime
    last_primary_mode_is_auto: bool

    # WD5-only fields:
    thermostat_id: int | None = None
    schedule: dict[str, Any] | None = None
    adaptive_mode: bool | None = None
    open_window_detection: bool | None = None
    daylight_saving_active: bool | None = None
    sensor_mode: int | None = None
    temperature_floor: int | None = None
    temperature_room: int | None = None
    boost_end_time: datetime | None = None
    vacation_mode: bool | None = None
    vacation_begin_time: datetime | None = None
    vacation_end_time: datetime | None = None
    vacation_temperature: int | None = None
    frost_protection_temperature: int | None = None
    boost_temperature: int | None = None
    energy: list[float] | None = None

    # WG4/WG5 fields:
    temperature: int | None = None
    set_point_temperature: int | None = None

    # WG5-only fields:
    building_id: str | None = None
    zone_uuid: str | None = None
    is_in_standby: bool | None = None
    schedule_id: str | None = None
    schedule_name: str | None = None

    @classmethod
    def from_wd5_json(cls, data: dict[str, Any]) -> Thermostat:
        """Return a new Thermostat instance based on JSON from the WD5-series API.

        Args:
        ----
            data: The JSON data from the API.

        Returns:
        -------
            A Thermostat Object.

        """
        time_zone = data["TimeZone"]
        return cls(
            thermostat_id=data["Id"],
            # Technically this could be a OWD5 or MWD5:
            model="OWD5",
            serial_number=data["SerialNumber"],
            software_version=data["SWversion"],
            zone_name=data["GroupName"],
            zone_id=data["GroupId"],
            name=data["ThermostatName"],
            online=data["Online"],
            heating=data["Heating"],
            regulation_mode=data["RegulationMode"],
            supported_regulation_modes=[
                REGULATION_SCHEDULE,
                REGULATION_COMFORT,
                REGULATION_MANUAL,
                REGULATION_VACATION,
                REGULATION_FROST_PROTECTION,
                REGULATION_BOOST,
                REGULATION_ECO,
            ],
            sensor_mode=data["SensorAppl"],
            adaptive_mode=data["AdaptiveMode"],
            open_window_detection=data["OpenWindow"],
            last_primary_mode_is_auto=data["LastPrimaryModeIsAuto"],
            daylight_saving_active=data["DaylightSavingActive"],
            temperature_floor=data["FloorTemperature"],
            temperature_room=data["RoomTemperature"],
            min_temperature=data["MinSetpoint"],
            max_temperature=data["MaxSetpoint"],
            comfort_temperature=data["ComfortSetpoint"],
            manual_temperature=data["ManualModeSetpoint"],
            frost_protection_temperature=data["FrostProtectionTemperature"],
            boost_temperature=data["MaxSetpoint"],
            boost_end_time=parse_wd5_date(data["BoostEndTime"], time_zone),
            comfort_end_time=parse_wd5_date(data["ComfortEndTime"], time_zone),
            vacation_mode=data["VacationEnabled"],
            vacation_begin_time=parse_wd5_date(data["VacationBeginDay"], time_zone),
            vacation_end_time=parse_wd5_date(data["VacationEndDay"], time_zone),
            vacation_temperature=data["VacationTemperature"],
            schedule=data["Schedule"],
        )

    @classmethod
    def from_wg4_json(cls, data: dict[str, Any]) -> Thermostat:
        """Return a new Thermostat instance based on JSON from the WG4-series API.

        The WG4 API does return schedule data but it is currently ignored.

        Args:
        ----
            data: The JSON data from the API.

        Returns:
        -------
            A Thermostat Object.

        """
        tz_offset = data["TZOffset"]
        return cls(
            # Technically this could be a UWG4 or AWG4:
            model="UWG4",
            serial_number=data["SerialNumber"],
            software_version=data["SWVersion"],
            zone_name=data["GroupName"],
            zone_id=data["GroupId"],
            name=data["Room"],
            online=data["Online"],
            heating=data["Heating"],
            regulation_mode=data["RegulationMode"],
            supported_regulation_modes=[
                REGULATION_SCHEDULE,
                REGULATION_COMFORT,
                REGULATION_MANUAL,
                REGULATION_VACATION,
            ],
            last_primary_mode_is_auto=data["LastPrimaryModeIsAuto"],
            temperature=data["Temperature"],
            set_point_temperature=data["SetPointTemp"],
            min_temperature=data["MinTemp"],
            max_temperature=data["MaxTemp"],
            comfort_temperature=data["ComfortTemperature"],
            manual_temperature=data["ManualTemperature"],
            comfort_end_time=parse_wg4_date(data["ComfortEndTime"]),
            vacation_mode=data["VacationEnabled"],
            # Most dates in the WG4 API are specified as UTC (+00:00) but
            # vacation begin & end are specified in local time with no offset.
            vacation_begin_time=parse_wg4_date(
                data["VacationBeginDay"] + " " + tz_offset
            ),
            vacation_end_time=parse_wg4_date(data["VacationEndDay"] + " " + tz_offset),
            vacation_temperature=data["VacationTemperature"],
        )

    @classmethod
    def from_wg5_json(
        cls,
        data: dict[str, Any],
        building_id: str,
        zone_name: str,
        *,
        away_active: bool = False,
    ) -> Thermostat:
        """Return a new Thermostat instance based on JSON from the WG5-series API.

        Args:
        ----
            data: The thermostat control JSON data from the API.
            building_id: The building UUID this thermostat belongs to.
            zone_name: The zone name this thermostat belongs to.
            away_active: Whether away mode is active for the building.

        Returns:
        -------
            A Thermostat Object.

        """
        mode = data["mode"]
        setpoint_centideg = round(data["setpoint"] * 100)

        if away_active or mode.get("isAwayActive"):
            regulation_mode = REGULATION_VACATION
        elif mode["isInStandby"]:
            regulation_mode = REGULATION_FROST_PROTECTION
        elif mode.get("fallbackMode") == "Auto":
            regulation_mode = REGULATION_SCHEDULE
        else:
            regulation_mode = REGULATION_MANUAL

        return cls(
            model="WG5",
            serial_number=data["id"],
            software_version="",
            zone_name=zone_name,
            zone_id=0,
            zone_uuid=data["zoneId"],
            building_id=building_id,
            name=data["name"],
            online=data["isOnline"],
            heating=data["isHeatRelayActive"],
            regulation_mode=regulation_mode,
            supported_regulation_modes=[
                REGULATION_SCHEDULE,
                REGULATION_MANUAL,
                REGULATION_COMFORT,
                REGULATION_VACATION,
                REGULATION_FROST_PROTECTION,
            ],
            min_temperature=round(data["minimumPossibleSetPoint"] * 100),
            max_temperature=round(data["maximumPossibleSetPoint"] * 100),
            temperature=round(data["currentTemperature"] * 100),
            set_point_temperature=setpoint_centideg,
            manual_temperature=setpoint_centideg,
            comfort_temperature=setpoint_centideg,
            comfort_end_time=datetime.min.replace(tzinfo=UTC),
            last_primary_mode_is_auto=mode.get("fallbackMode") == "Auto",
            frost_protection_temperature=round(
                data["frostProtectionTemperature"] * 100
            ),
            sensor_mode=WG5_SENSOR_MAP.get(data.get("sensorApplication", "")),
            open_window_detection=data.get("isOpenWindowDetected", False),
            vacation_mode=away_active or mode.get("isAwayActive", False),
            is_in_standby=mode["isInStandby"],
            schedule_id=data.get("scheduleData", {}).get("scheduleId"),
            schedule_name=data.get("scheduleData", {}).get("scheduleName"),
        )

    def get_target_temperature(self) -> int:
        """Return the target temperature for the thermostat.

        Returns
        -------
            The current temperature.

        """
        # The WG4 API makes this easy:
        if self.set_point_temperature is not None:
            return self.set_point_temperature

        # The OWD5 API requires computing the value:
        if self.regulation_mode == REGULATION_SCHEDULE and self.schedule:
            return Schedule.from_json(self.schedule).get_active_temperature()
        if self.regulation_mode == REGULATION_COMFORT:
            return self.comfort_temperature
        if self.regulation_mode == REGULATION_MANUAL:
            return self.manual_temperature
        if self.regulation_mode == REGULATION_VACATION:
            return self.vacation_temperature or 0
        if self.regulation_mode == REGULATION_FROST_PROTECTION:
            return self.frost_protection_temperature or 0
        if self.regulation_mode == REGULATION_BOOST:
            return self.boost_temperature or 0
        if self.regulation_mode == REGULATION_ECO and self.schedule:
            return Schedule.from_json(self.schedule).get_lowest_temperature()

        return 0

    def get_current_temperature(self) -> int:
        """Return the current temperature for the thermostat.

        For WD5-series thermostats, the current temperature based on the
        sensor type. If it is set to room/floor, then the average is used.

        Returns
        -------
            The current temperature.

        """
        # Once again, this is easy with the WG4 API:
        if self.temperature is not None:
            return self.temperature

        if self.sensor_mode == SENSOR_ROOM:
            return self.temperature_room or 0
        if self.sensor_mode == SENSOR_FLOOR:
            return self.temperature_floor or 0
        if self.sensor_mode == SENSOR_ROOM_FLOOR:
            return ceil(
                ((self.temperature_floor or 0) + (self.temperature_room or 0)) / 2
            )

        return 0

    def get_current_energy(self) -> float:
        """Return the current energy usage for the thermostat.

        Returns
        -------
            The current energy usage in kWh.

        """
        if self.energy is not None:
            return self.energy[0]

        return 0.0


def parse_wd5_date(value: str, offset: int) -> datetime:
    """Parse a given value and offset into a datetime object.

    Args:
    ----
        value: The date time string.
        offset: The time zone offset in seconds.

    Returns:
    -------
        The parsed datetime.

    """
    timezone = strftime("%H:%M", gmtime(abs(offset)))
    separator = "+" if offset >= 0 else "-"

    value = f"{value}{separator}{timezone}"
    seconds = timedelta(seconds=offset)

    if offset >= 0:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z") - seconds

    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z") + seconds


def parse_wg4_date(value: str) -> datetime:
    """Fix corrupt date formats used by the WG4 API.

    Bizarrely the WG4 API sometimes--but not always--sends dates of the form:
    28/01/2024 23:29:00 +00:00 +00:00

    Args:
    ----
        value: The raw date string from API JSON.

    Returns:
    -------
        The parsed datetime.

    """
    value = "+".join(value.split("+")[:2]).strip()

    return datetime.strptime(value, WG4_DATETIME_FORMAT).astimezone()
