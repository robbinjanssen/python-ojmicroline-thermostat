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


async def main() -> None:
    """Show example on using the OJ Electronics API client."""
    async with OJMicroline(  # noqa: S106
        host="ocd5.azurewebsites.net",
        customer_id=99,
        api_key="",
        username="",
        password="",
    ) as client:
        thermostats = await client.get_thermostats()
        for item in thermostats:
            print(f"Name: {item.name}")
            print(f"Mode: {REGULATION_MODES[item.regulation_mode]}")
            print(f"Current temp: {item.get_current_temperature()}")
            print(f"Target temp: {item.get_target_temperature()}")
            print(item)
            print("---")

        # Set first thermostat to another mode.
        await client.set_regulation_mode(thermostats[0], REGULATION_BOOST)
        # await client.set_regulation_mode(thermostats[0], REGULATION_SCHEDULE)
        # await client.set_regulation_mode(thermostats[0], REGULATION_MANUAL, 2000)
        # await client.set_regulation_mode(thermostats[0], REGULATION_COMFORT, 2000)
        # await client.set_regulation_mode(thermostats[0], REGULATION_ECO)
        print(f"Name: {thermostats[0].name}")
        print(f"Mode: {REGULATION_MODES[thermostats[0].regulation_mode]}")
        print(f"Current temp: {thermostats[0].get_current_temperature()}")
        print(f"Target temp: {thermostats[0].get_target_temperature()}")
        print(thermostats[0])
        print("---")


if __name__ == "__main__":
    asyncio.run(main())
