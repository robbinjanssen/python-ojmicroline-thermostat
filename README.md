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

A python package with which you control your OJ Microline Thermostat. Currently it supports reading temperatures
and various details about the thermostat(s) and setting the thermostat to another regulation mode.

## Installation

```bash
pip install ojmicroline-thermostat
```

## Datasets

### Thermostat

This set represents the current state of your thermostat.

| Variable | Type | Description |
| :------- | :--- | :---------- |
| `thermostat_id` | int | The unique identifier for this thermostat. |
| `model` | string | The model name for this thermostat. |
| `serial_number` | string | The serial number for this thermostat. |
| `software_version` | string | The currently installed software version. |
| `zone_name` | string | The name of the zone this thermostat belongs to. |
| `zone_id` | integer | The ID of the zone this thermostat belongs to. |
| `name` | string | The name of the thermostat. |
| `online` | boolean | Indicates if the thermostat is connected to the network. |
| `heating` | boolean | Indicates if the thermostat is currently heating/is on. |
| `adaptive_mode` | boolean | If on then then the thermostat automatically changes heating start times to ensure that the required temperature has been reached at the beginning of any specific event. |
| `vacation_mode` | boolean | If on then the thermostat regulates the heating of your home to a minimum while you are away on holiday, thus saving energy and money. |
| `open_window_detection` | boolean | If on then the thermostat shuts off the heating for 30 minutes if an open window is detected. |
| `last_primary_mode_is_auto` | boolean | Unknown |
| `daylight_saving_active` | boolean | If on, the "Daylight Saving Time" function of the thermostat will automatically adjust the clock to the daylight saving time for the "Region" chosen. |
| `regulation_mode` | integer | The currently set regulation mode of the thermostat, see below. |
| `sensor_mode` | integer | The currently set sensor mode of the thermostat, see below. |
| `temperature_floor` | integer | The temperature measured by the floor. sensor |
| `temperature_room` | integer | The temperature measured by the room sensor. |
| `min_temperature` | integer | The minimum set temperature for the thermostat. |
| `max_temperature` | integer | The maximum set temperature for the thermostat. |
| `temperatures` | object | The currently set temperatures for each regulation mode, see below. |
| `boost_end_time` | datetime | If the regulation mode is set to boost mode, it will end at this time. |
| `comfort_end_time` | datetime | If the regulation mode is set to comfort mode, it will end at this time. |
| `vacation_begin_time` | datetime | Vacation mode will be set to on when this date time passes. |
| `vacation_end_time` | datetime | Vacation mode will be set to off when this date time passes. |
| `offset` | integer | The offset (timezone) set by the thermostat. |
| `schedule` | Schedule | The schedule the thermostat currently uses. |

#### Regulation modes

| Integer | Constant | Description |
| :------- | :--- | :---------- |
| `1` | `REGULATION_SCHEDULE` | The thermostat follows the configured schedule. |
| `2` | `REGULATION_COMFORT` | The thermostat is in comfort mode for the next 4 hours. |
| `3` | `REGULATION_MANUAL` | The thermostat is in manual mode, will not resume schedule unless changed. |
| `4` | `REGULATION_VACATION` | The thermostat is in vacation mode, it started at `vacation_begin_time` and ends at `vacation_end_time`. |
| `6` | `REGULATION_FROST_PROTECTION` | The thermostat is set to frost protection, preventing the floor from freezing. |
| `8` | `REGULATION_BOOST` | The thermostat is in boost mode for 1 hour, using the `max_temperature` as target temperature. |
| `9` | `REGULATION_ECO` | The thermostat is in eco mode, using the lowest temperature of the `schedule`. |

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
| `set_regulation_mode` | `resource: Thermostat`, `regulation_mode: int`, `temperature: int \| None = None` | Set the regulation mode based on the input.<br> - `resource`: An instance of a Thermostat model returned by `get_thermostats()`<br> - `regulation_mode`: An integer representing the regulation mode, see "Regulation modes"<br> - `temperature`: An integer representing the temperature, eg: 2500. Only useful when setting the regulation mode to manual or comfort. |

## Usage

```python
import asyncio
from time import sleep

from ojmicroline_thermostat import OJMicroline, Thermostat
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


async def main():
    """Show example on using the OJMicroline client."""
    async with OJMicroline(
        host="ocd5.azurewebsites.net",
        customer_id=1234,
        api_key="<app-api-key>",
        username="<your-username>",
        password="<your-password>",
    ) as client:
        # Thermostats
        thermostats: list[Thermostat] = await client.get_thermostats()

        for resource in thermostats:
            print("####################")
            print(f"# {resource.name}")
            print("####################")
            print("- Details:")
            print(f"   Serial Number: {resource.serial_number}")
            print(f"   Sensor mode: {SENSOR_MODES[resource.sensor_mode]}")
            print("- Regulation:")
            print(f"   Mode: {REGULATION_MODES[resource.regulation_mode]}")
            print(f"   Temperature: {resource.get_current_temperature()}")
            print(f"   Target temperature: {resource.get_target_temperature()}")
            print("- Temperatures:")
            print(f"   Floor: {resource.temperature_floor}")
            print(f"   Room: {resource.temperature_room}")
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

Copyright (c) 2020-2023 Robbin Janssen

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
[maintenance-shield]: https://img.shields.io/maintenance/yes/2023.svg
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
