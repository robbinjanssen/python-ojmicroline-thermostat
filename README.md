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

<details>
    <summary>Click here to get more details</summary>

### Thermostat

This set represents the current state of your thermostat.

**NOTE**: Not all parking garages have data for long-term parking.

| Variable | Type | Description |
| :------- | :--- | :---------- |
| `garage_id` | string | The id of the garage |
| `garage_name` | string | The name of the garage |
| `state` | string | The state of the garage (`ok` or `problem`) |
| `free_space_short` | integer | The number of free spaces for day visitors |
| `free_space_long` | integer (or None) | The number of free spaces for season ticket holders |
| `short_capacity` | integer | The total capacity of the garage for day visitors |
| `long_capacity` | integer (or None) | The total capacity of the garage for season ticket holders |
| `availability_pct` | float | The percentage of free parking spaces |
| `longitude` | float | The longitude of the garage |
| `latitude` | float | The latitude of the garage |

</details>

## Usage

```python
import asyncio
from time import sleep

from ojmicroline_thermostat import OJMicroline

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

            print(f"- Setting to {REGULATION_MODES[REGULATION_BOOST]}")
            await client.set_regulation_mode(resource, REGULATION_BOOST)
            print("Sleeping for 5 seconds..")
            sleep(5)
            print(f"- Setting to {REGULATION_MODES[REGULATION_SCHEDULE]}")  # noqa: E501
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
