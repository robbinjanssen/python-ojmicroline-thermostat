# pylint: disable=W0621
"""Asynchronous Python client for OJ Electronics API."""

import asyncio

from ojmicroline_thermostat import OJMicroline
from ojmicroline_thermostat.const import (
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
    DATETIME_FORMAT,
)

REGULATION_MODES = {
    REGULATION_SCHEDULE: "Schedule",
    REGULATION_ECO: "Eco",
    REGULATION_MANUAL: "None (Manual)",
    REGULATION_FROST_PROTECTION: "Frost Protection",
    REGULATION_BOOST: "Boost",
    REGULATION_COMFORT: "Comfort",
    REGULATION_VACATION: "Vacation",
}

SENSOR_MODES = {
    SENSOR_FLOOR: "Floor",
    SENSOR_ROOM: "Room",
    SENSOR_ROOM_FLOOR: "Room/Floor",
}


async def main() -> None:
    """Show example on using the OJ Electronics API client."""
    async with OJMicroline(  # noqa: S106
        host="ocd5.azurewebsites.net",
        customer_id=99,
        api_key="app-api-key",
        username="username",
        password="password",
    ) as client:
        thermostats = await client.get_thermostats()
        for resource in thermostats:
            print("####################")
            print(f"# {resource.name}")
            print("####################")
            print("- Details:")
            print(f"   ID: {resource.thermostat_id}")
            print(f"   Model: {resource.model}")
            print(f"   Serial Number: {resource.serial_number}")
            print(f"   Software Version: {resource.software_version}")
            print(f"   Zone: {resource.zone_name} ({resource.zone_id})")
            print(f"   Sensor mode: {SENSOR_MODES[resource.sensor_mode]}")
            print("- Regulation:")
            print(f"   Mode: {REGULATION_MODES[resource.regulation_mode]}")
            print(f"   Temperature: {resource.get_current_temperature()}")
            print(f"   Target temperature: {resource.get_target_temperature()}")
            print("- Temperatures:")
            print(f"   Floor: {resource.temperature_floor}")
            print(f"   Room: {resource.temperature_room}")
            print(f"   Min: {resource.min_temperature}")
            print(f"   Max: {resource.max_temperature}")
            for mode in REGULATION_MODES:
                print(f"   {REGULATION_MODES[mode]}: {resource.temperatures[mode]}")
            print("- Dates:")
            print(f"   Comfort End: {resource.comfort_end_time.strftime(DATETIME_FORMAT)}")  # noqa: E501
            print(f"   Boost End: {resource.boost_end_time.strftime(DATETIME_FORMAT)}")
            print(f"   Vacation Begin: {resource.vacation_begin_time.strftime(DATETIME_FORMAT)}")  # noqa: E501
            print(f"   Vacation End: {resource.vacation_end_time.strftime(DATETIME_FORMAT)}")  # noqa: E501
            print("- Status:")
            print(f"   Online: {resource.online}")
            print(f"   Heating: {resource.heating}")
            print(f"   Adaptive Mode: {resource.adaptive_mode}")
            print(f"   Vacation Mode: {resource.vacation_mode}")
            print(f"   Open Window Detection: {resource.open_window_detection}")
            print(f"   Daylight Saving: {resource.daylight_saving_active}")
            print(f"   Last Primary Mode is auto: {resource.last_primary_mode_is_auto}")
            print("")


if __name__ == "__main__":
    asyncio.run(main())
