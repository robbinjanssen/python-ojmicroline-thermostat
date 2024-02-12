[![GitHub Release][releases-shield]][releases]
[![Python Versions][python-versions-shield]][pypi]
![Project Stage][project-stage-shield]
![Project Maintenance][maintenance-shield]
[![License][license-shield]](LICENSE)

[![GitHub Activity][commits-shield]][commits-url]
[![PyPi Downloads][downloads-shield]][downloads-url]
[![GitHub Last Commit][last-commit-shield]][commits-url]
[![Stargazers][stars-shield]][stars-url]
[![Issues][issues-shield]][issues-url]

[![Maintainability][maintainability-shield]][maintainability-url]
[![Code Coverage][codecov-shield]][codecov-url]

[![Build Status][build-shield]][build-url]
[![Typing Status][typing-shield]][typing-url]

Asynchronous Python client for communicating with a OJ Microline Thermostat.

## About

A Python package to control OJ Microline thermostats. It currently supports the WD5 series (OWD5, MWD5) and WG4 series (UWG4, AWG4).

## Installation

```bash
pip install ojmicroline-thermostat
```

## Datasets

### Thermostat

This object represents the current state of a thermostat.

| Variable | Type | Description |
| :------- | :--- | :---------- |
| `model` | string | The model name for this thermostat. |
| `serial_number` | string | The serial number for this thermostat. |
| `software_version` | string | The currently installed software version. |
| `zone_name` | string | The name of the zone this thermostat belongs to. |
| `zone_id` | integer | The ID of the zone this thermostat belongs to. |
| `name` | string | The name of the thermostat. |
| `online` | boolean | Indicates if the thermostat is connected to the network. |
| `heating` | boolean | Indicates if the thermostat is currently heating/is on. |
| `regulation_mode` | integer | The currently set regulation mode of the thermostat, see below. |
| `supported_regulation_modes` | list | The regulation modes this thermostat supports. |
| `min_temperature` | integer | The lowest temperature the thermostat can be set to. |
| `max_temperature` | integer | The highest temperature the thermostat can be set to. |
| `manual_temperature` | integer | If the regulation mode is set to manual mode, the thermostat will target this temperature. |
| `comfort_temperature` | integer | If the regulation mode is set to comfort mode, the thermostat will target this temperature. |
| `comfort_end_time` | datetime | If the regulation mode is set to comfort mode, it will end at this time. |
| `vacation_mode` | boolean | If true then the thermostat is set to `vacation_temperature` from `vacation_begin_time` to `vacation_end_time`. If false, then no vacation is in progress or scheduled. |
| `vacation_temperature` | integer | If the regulation mode is VACATION, the thermostat will target this temperature. |
| `vacation_begin_time` | datetime | The vacation regulation mode will begin at this time. Note that vacations may be scheduled to begin in the future. |
| `vacation_end_time` | datetime | If in vacation mode, the thermostat will return to schedule mode at this time. |
| `last_primary_mode_is_auto` | boolean | Unknown |

These fields are only available on WD5-series thermostats; for others, they may be `None`:

| Variable | Type | Description |
| :------- | :--- | :---------- |
| `thermostat_id` | int | The unique identifier for this thermostat. |
| `adaptive_mode` | boolean | If on then then the thermostat automatically changes heating start times to ensure that the required temperature has been reached at the beginning of any specific event. |
| `open_window_detection` | boolean | If on then the thermostat shuts off the heating for 30 minutes if an open window is detected. |
| `daylight_saving_active` | boolean | If on, the "Daylight Saving Time" function of the thermostat will automatically adjust the clock to the daylight saving time for the "Region" chosen. |
| `sensor_mode` | integer | The currently set sensor mode of the thermostat, see below. |
| `temperature_floor` | integer | The temperature measured by the floor sensor. |
| `temperature_room` | integer | The temperature measured by the room sensor. |
| `boost_temperature` | integer | If the regulation mode is set to boost mode, the thermostat will target this temperature. |
| `boost_end_time` | datetime | If the regulation mode is set to boost mode, it will end at this time. |
| `frost_protection_temperature` | integer | If the regulation mode is set to frost protection mode, the thermostat will target this temperature. |
| `schedule` | Schedule | The schedule the thermostat currently uses. (This *could* be supported by WG4-series thermostats, it simply isn't implemented.) |

These fields are only available on WG4-series thermostats; for others, they may be `None`:

| Variable | Type | Description |
| :------- | :--- | :---------- |
| `temperature` | integer | The current temperature; the thermostat uses the room sensor or floor sensor based on its configuration. Avoid using this directly; instead, call the `get_current_temperature()` method which also works for WD5-series thermostats. |
| `set_point_temperature` | integer | The temperature the thermostat is targeting. Avoid using this directly; instead, call the `get_target_temperature()` method which also works for WD5-series thermostats. |

#### Regulation modes

| Integer | Constant | Description |
| :------- | :--- | :---------- |
| `1` | `REGULATION_SCHEDULE` | The thermostat follows the configured schedule. |
| `2` | `REGULATION_COMFORT` | The thermostat is in comfort mode until `comfort_end_time`. |
| `3` | `REGULATION_MANUAL` | The thermostat is in manual mode, will not resume schedule unless changed. |
| `4` | `REGULATION_VACATION` | The thermostat is in vacation mode, it started at `vacation_begin_time` and ends at `vacation_end_time`. |
| `6` | `REGULATION_FROST_PROTECTION` | The thermostat is set to frost protection, preventing the floor from freezing. |
| `8` | `REGULATION_BOOST` | The thermostat is in boost mode for 1 hour, using the `max_temperature` as target temperature. |
| `9` | `REGULATION_ECO` | The thermostat is in eco mode, using the lowest temperature of the `schedule`. |

Keep in mind that certain thermostats only support a subset of these modes; be sure to check the `Thermostat.supported_regulation_modes` field.

#### Sensor modes

| Integer | Constant | Description |
| :------- | :--- | :---------- |
| `1` | `SENSOR_ROOM_FLOOR` | The thermostat takes the average of the room and floor temperature |
| `3` | `SENSOR_FLOOR` | The thermostat is using the floor sensor for target temperature. |
| `4` | `SENSOR_ROOM` | The thermostat is using the room sensor for target temperature. |

## Methods

### OJMicroline
| Method | Params | Description |
| :------- | :--- | :---------- |
| `login` | `None` | Create a new session at the OJ Microline API. |
| `get_thermostats` | `None` | Get all thermostats from the OJ Microline API. |
| `set_regulation_mode` | `resource: Thermostat`, `regulation_mode: int`, `temperature: int \| None = None`, `duration: int = COMFORT_DURATION` | Set the regulation mode based on the input.<br> - `resource`: An instance of a Thermostat model returned by `get_thermostats()`<br> - `regulation_mode`: An integer representing the regulation mode, see "Regulation modes"<br> - `temperature`: An integer representing the temperature, eg: 2500. Only useful when setting the regulation mode to manual or comfort.<br> - `duration`: The duration in minutes to set the temperature for; only applies to comfort mode. |

## Usage

```python
import asyncio
from time import sleep

from ojmicroline_thermostat import OJMicroline, Thermostat, WD5API
from ojmicroline_thermostat.const import (
    REGULATION_BOOST,
    REGULATION_COMFORT,
    REGULATION_ECO,
    REGULATION_FROST_PROTECTION,
    REGULATION_MANUAL,
    REGULATION_SCHEDULE,
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


async def main():
    """Show example on using the OJMicroline client."""
    async with OJMicroline(
        api=WD5API(
            customer_id=1234,
            api_key="<app-api-key>",
            username="<your-username>",
            password="<your-password>",
        ),
    ) as client:
        # Thermostats
        thermostats: list[Thermostat] = await client.get_thermostats()

        for resource in thermostats:
            print("####################")
            print(f"# {resource.name}")
            print("####################")
            print("- Details:")
            print(f"   Serial Number: {resource.serial_number}")
            print(f"   Mode: {REGULATION_MODES[resource.regulation_mode]}")
            print(f"   Temperature: {resource.get_current_temperature()}")
            print(f"   Target temperature: {resource.get_target_temperature()}")
            print("")

            print(f"- Setting to boost mode")
            await client.set_regulation_mode(resource, REGULATION_BOOST)
            print("Sleeping for 5 seconds..")
            sleep(5)
            print(f"- Setting to schedule")  # noqa: E501
            await client.set_regulation_mode(resource, REGULATION_SCHEDULE)


if __name__ == "__main__":
    asyncio.run(main())
```

## Contributing

This is an active open-source project. We are always open to people who want to
use the code or contribute to it.

We've set up a separate document for our
[contribution guidelines](CONTRIBUTING.md).

Thank you for being involved! :heart_eyes:

## Setting up development environment

This Python project is fully managed using the [Poetry][poetry] dependency
manager.

You need at least:

- Python 3.9+
- [Poetry][poetry-install]

Install all packages, including all development requirements:

```bash
poetry install
```

Poetry creates by default an virtual environment where it installs all
necessary pip packages, to enter or exit the venv run the following commands:

```bash
poetry shell
exit
```

Setup the pre-commit check, you must run this inside the virtual environment:

```bash
pre-commit install
```

*Now you're all set to get started!*

As this repository uses the [pre-commit][pre-commit] framework, all changes
are linted and tested with each commit. You can run all checks and tests
manually, using the following command:

```bash
poetry run pre-commit run --all-files
```

To run just the Python tests:

```bash
poetry run pytest
```

## License

MIT License

Copyright (c) 2020-2024 Robbin Janssen

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

<!-- MARKDOWN LINKS & IMAGES -->
[build-shield]: https://github.com/robbinjanssen/python-ojmicroline-thermostat/actions/workflows/tests.yaml/badge.svg
[build-url]: https://github.com/robbinjanssen/python-ojmicroline-thermostat/actions/workflows/tests.yaml
[commits-shield]: https://img.shields.io/github/commit-activity/y/robbinjanssen/python-ojmicroline-thermostat.svg
[commits-url]: https://github.com/robbinjanssen/python-ojmicroline-thermostat/commits/main
[codecov-shield]: https://codecov.io/gh/robbinjanssen/python-ojmicroline-thermostat/branch/main/graph/badge.svg?token=F6CE1S25NV
[codecov-url]: https://codecov.io/gh/robbinjanssen/python-ojmicroline-thermostat
[downloads-shield]: https://img.shields.io/pypi/dm/ojmicroline-thermostat
[downloads-url]: https://pypistats.org/packages/ojmicroline-thermostat
[issues-shield]: https://img.shields.io/github/issues/robbinjanssen/python-ojmicroline-thermostat.svg
[issues-url]: https://github.com/robbinjanssen/python-ojmicroline-thermostat/issues
[license-shield]: https://img.shields.io/github/license/robbinjanssen/python-ojmicroline-thermostat.svg
[last-commit-shield]: https://img.shields.io/github/last-commit/robbinjanssen/python-ojmicroline-thermostat.svg
[maintenance-shield]: https://img.shields.io/maintenance/yes/2024.svg
[maintainability-shield]: https://api.codeclimate.com/v1/badges/d77f7409eb02e331261b/maintainability
[maintainability-url]: https://codeclimate.com/github/robbinjanssen/python-ojmicroline-thermostat/maintainability
[project-stage-shield]: https://img.shields.io/badge/project%20stage-experimental-yellow.svg
[pypi]: https://pypi.org/project/ojmicroline-thermostat/
[python-versions-shield]: https://img.shields.io/pypi/pyversions/ojmicroline-thermostat
[typing-shield]: https://github.com/robbinjanssen/python-ojmicroline-thermostat/actions/workflows/typing.yaml/badge.svg
[typing-url]: https://github.com/robbinjanssen/python-ojmicroline-thermostat/actions/workflows/typing.yaml
[releases-shield]: https://img.shields.io/github/release/robbinjanssen/python-ojmicroline-thermostat.svg
[releases]: https://github.com/robbinjanssen/python-ojmicroline-thermostat/releases
[stars-shield]: https://img.shields.io/github/stars/robbinjanssen/python-ojmicroline-thermostat.svg
[stars-url]: https://github.com/robbinjanssen/python-ojmicroline-thermostat/stargazers

[poetry-install]: https://python-poetry.org/docs/#installation
[poetry]: https://python-poetry.org
[pre-commit]: https://pre-commit.com
