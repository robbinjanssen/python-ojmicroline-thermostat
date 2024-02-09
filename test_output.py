# ruff: noqa: S106, E501, PLR0915
# pylint: disable=too-many-statements
"""Asynchronous Python client for OJ Microline Thermostat."""
import asyncio
from asyncio import sleep

from ojmicroline_thermostat import WD5API, OJMicroline
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

DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


async def main() -> None:
    """Show example on using the OJ Microline client."""
    async with OJMicroline(
        api=WD5API(
            customer_id=99,
            api_key="<app-api-key>",
            username="<your-username>",
            password="<your-password>",
        ),
    ) as client:
        # fmt: off
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
            if resource.sensor_mode is not None:
                print(f"   Sensor mode: {SENSOR_MODES[resource.sensor_mode]}")
            print("- Regulation:")
            print(f"   Mode: {REGULATION_MODES[resource.regulation_mode]}")
            print(f"   Temperature: {resource.get_current_temperature()}")
            print(f"   Target temperature: {resource.get_target_temperature()}")
            print("- Temperatures:")
            print(f"   Current: {resource.get_current_temperature()}")
            print(f"   Target: {resource.get_target_temperature()}")
            print(f"   Range: {resource.min_temperature} - {resource.max_temperature}")
            print("- Dates:")
            print(f"   Comfort End: {resource.comfort_end_time.strftime(DATETIME_FORMAT)}")
            if resource.boost_end_time is not None:
                print(f"   Boost End: {resource.boost_end_time.strftime(DATETIME_FORMAT)}")
            if resource.vacation_begin_time is not None:
                print(f"   Vacation Begin: {resource.vacation_begin_time.strftime(DATETIME_FORMAT)}")
            if resource.vacation_end_time is not None:
                print(f"   Vacation End: {resource.vacation_end_time.strftime(DATETIME_FORMAT)}")
            print("- Status:")
            print(f"   Online: {resource.online}")
            print(f"   Heating: {resource.heating}")
            print(f"   Adaptive Mode: {resource.adaptive_mode}")
            if resource.vacation_mode is not None:
                print(f"   Vacation Mode: {resource.vacation_mode}")
            if resource.open_window_detection is not None:
                print(f"   Open Window Detection: {resource.open_window_detection}")
            if resource.daylight_saving_active is not None:
                print(f"   Daylight Saving: {resource.daylight_saving_active}")
            print(f"   Last Primary Mode is auto: {resource.last_primary_mode_is_auto}")
            print("")

            await sleep(5)
            print(f"Updating the preset mode for {resource.name}")
            print(f"Current: {REGULATION_MODES[resource.regulation_mode]}")

            print(f"- Setting to {REGULATION_MODES[REGULATION_COMFORT]} and temperature 2500")
            await client.set_regulation_mode(resource, REGULATION_COMFORT, 2500)
            print("Sleeping for 5 seconds..")
            await sleep(5)

            if REGULATION_BOOST in resource.supported_regulation_modes:
                print(f"- Setting to {REGULATION_MODES[REGULATION_BOOST]}")
                await client.set_regulation_mode(resource, REGULATION_BOOST)
                print("Sleeping for 5 seconds..")
                await sleep(5)

            print(f"- Setting to {REGULATION_MODES[REGULATION_COMFORT]} and temperature 2500")
            await client.set_regulation_mode(resource, REGULATION_COMFORT, 2500)
            print("Sleeping for 5 seconds..")
            await sleep(5)

            print(f"- Setting to {REGULATION_MODES[REGULATION_SCHEDULE]}")
            await client.set_regulation_mode(resource, REGULATION_SCHEDULE)
            print("Sleeping for 5 seconds..")
            await sleep(5)
        # fmt: on


if __name__ == "__main__":
    asyncio.run(main())
