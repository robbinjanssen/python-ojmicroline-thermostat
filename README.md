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

A Python package to control OJ Microline thermostats. It currently supports the WD5 series (OWD5, MWD5), WG4 series (UWG4, AWG4), and WG5 series.

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
| `open_window_detection` | boolean | If on then the thermostat shuts off the heating for 30 minutes if an open window is detected. Also available on WG5-series thermostats. |
| `daylight_saving_active` | boolean | If on, the "Daylight Saving Time" function of the thermostat will automatically adjust the clock to the daylight saving time for the "Region" chosen. |
| `sensor_mode` | integer | The currently set sensor mode of the thermostat, see below. Also available on WG5-series thermostats. |
| `temperature_floor` | integer | The temperature measured by the floor sensor. Also available on WG5-series thermostats. |
| `temperature_room` | integer | The temperature measured by the room sensor. Also available on WG5-series thermostats. |
| `boost_temperature` | integer | If the regulation mode is set to boost mode, the thermostat will target this temperature. |
| `boost_end_time` | datetime | If the regulation mode is set to boost mode, it will end at this time. |
| `frost_protection_temperature` | integer | If the regulation mode is set to frost protection mode, the thermostat will target this temperature. Also available on WG5-series thermostats. |
| `schedule` | Schedule/dict | The schedule the thermostat currently uses. On WD5 this is a `Schedule` object; on WG5 it is a raw dict from the API. |
| `energy` | list | The energy usage in kWh for the current day and the six previous days. (Note that the integrated tariff calculation needs to be disabled.) Also available on WG4-series thermostats. |

These fields are available on WG4 and WG5-series thermostats; for others, they may be `None`:

| Variable | Type | Description |
| :------- | :--- | :---------- |
| `temperature` | integer | The current temperature; the thermostat uses the room sensor or floor sensor based on its configuration. Avoid using this directly; instead, call the `get_current_temperature()` method which also works for WD5-series thermostats. |
| `set_point_temperature` | integer | The temperature the thermostat is targeting. Avoid using this directly; instead, call the `get_target_temperature()` method which also works for WD5-series thermostats. |

These fields are only available on WG4-series thermostats; for others, they may be `None`:

| Variable | Type | Description |
| :------- | :--- | :---------- |
| `utc_offset` | timedelta | The offset of the thermostat's time zone from UTC. |
| `load_watts` | integer | The load of the heating element in W, either measured by the thermostat or set manually during installation. Avoid using this directly; instead, call the `get_current_power()` method. |
| `energy_price` | float | The price per kWh that the app uses to show the energy costs (`KwhCharge`). |

These fields are only available on WG5-series thermostats; for others, they may be `None`:

| Variable | Type | Description |
| :------- | :--- | :---------- |
| `building_id` | string | The building UUID this thermostat belongs to. |
| `zone_uuid` | string | The zone UUID this thermostat belongs to (WG5 uses UUIDs rather than integer zone IDs). |
| `is_in_standby` | boolean | Whether the thermostat is in standby (frost protection) mode. |
| `schedule_id` | string | The UUID of the schedule assigned to this thermostat. |
| `schedule_name` | string | The name of the schedule assigned to this thermostat. |

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
| `get_current_energy` | None | Return the current energy usage in kWh. This is the first value in the `energy` property list. |
| `get_current_power` | None | Return the current power draw of the heating in W: the load while heating, and zero otherwise. Returns `None` if the load is unknown (only WG4-series thermostats report it). |

### WG4API
| Method | Params | Description |
| :------- | :--- | :---------- |
| `set_energy_price` | `resource: Thermostat`, `price: float` | Set the price per kWh that the app uses to show the energy costs, rounded to two decimals (the thermostat keeps two). The cloud applies the price to all stored usage, including earlier days. Requires a session (call `OJMicroline.login()` first). |

## Usage

### WD5 Example

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

### WG5 Example

```python
import asyncio

from ojmicroline_thermostat import OJMicroline, Thermostat
from ojmicroline_thermostat.wg5 import WG5API
from ojmicroline_thermostat.const import (
    REGULATION_MANUAL,
    REGULATION_SCHEDULE,
)


async def main():
    """Show example on using the OJMicroline client with a WG5 thermostat."""
    async with OJMicroline(
        api=WG5API(
            username="<your-username>",
            password="<your-password>",
        ),
    ) as client:
        thermostats: list[Thermostat] = await client.get_thermostats()

        for resource in thermostats:
            print(f"{resource.name}: {resource.get_current_temperature() / 100}°C")
            print(f"  Target: {resource.get_target_temperature() / 100}°C")

            # Set to manual mode at 25°C
            await client.set_regulation_mode(resource, REGULATION_MANUAL, 2500)

            # Set back to schedule
            await client.set_regulation_mode(resource, REGULATION_SCHEDULE)


if __name__ == "__main__":
    asyncio.run(main())
```

### Push notifications (WG4)

WG4-series thermostats can report changes as they happen, instead of being
polled. Subscribe a listener and the client keeps a background task waiting
for notifications from the API, retrying with an increasing delay if the
connection fails. The listener may be a plain function or a coroutine
function. Use `OJMicrolineAPI.supports_notifications` to check whether the
API in use supports this; `subscribe()` raises `OJMicrolineError` otherwise.

```python
import asyncio

from ojmicroline_thermostat import OJMicroline, Thermostat, WG4API


def on_update(thermostat: Thermostat) -> None:
    print(f"{thermostat.name}: {thermostat.get_current_temperature() / 100}°C")


async def main():
    async with OJMicroline(
        api=WG4API(username="<your-username>", password="<your-password>"),
    ) as client:
        unsubscribe = client.subscribe(on_update)
        await asyncio.sleep(600)
        unsubscribe()


if __name__ == "__main__":
    asyncio.run(main())
```

The WG4 API delivers notifications through long-polling, so a change made on
the thermostat or through the app usually reaches the listener within a few
seconds, and periodic temperature reports arrive roughly every minute per
thermostat. Notifications are queued per session, so the listener receives
every thermostat once when the subscription starts and again whenever the
client has to log in again.

### Local gateway (WG4 and WD5, experimental)

A thermostat keeps a connection open to OJ's device gateway and does not check
that gateway's certificate. If a DNS record on your network points the
hostname of that gateway at the machine that runs this library, the thermostat
connects to a gateway of this library instead. The API that goes with it then
reads and sets the thermostat without the cloud.

| Series | Hostname the thermostat looks for | Port | Gateway | API |
| :----- | :-------------------------------- | :--- | :------ | :-- |
| WG4 | `na-thermos.ojelectronics.com` (`WG4_NORTH_AMERICA`) | 9533 | `WG4Gateway` | `WG4LocalAPI` |
| WD5 | `owd5-r1099-thermostat.ojelectronics.com` for the SWATT app (`WD5_R1099`), `owd5-sl003-thermostat.ojelectronics.com` for Schlüter in Europe (`WD5_SL003`) | 9513 | `WD5Gateway` | `WD5LocalAPI` |

This is experimental. The WG4 gateway has run against one real thermostat.
The connection, the relay, recording the profile, answering the thermostat
locally and falling back to that while OJ's gateway was unreachable worked.
In the first test the thermostat acknowledged commands too late; in the
second it acknowledged all of them in time, and why they differed is not
known. The WD5 gateway has only run against a simulated thermostat.

The gateway has two modes:

| Mode | What the gateway does | The vendor's app |
| :--- | :-------------------- | :--------------- |
| `GatewayMode.RELAY` | Passes the connection on to OJ's gateway, and records the answers OJ's gateway gives the thermostat as its profile. When OJ's gateway cannot be used, a thermostat that has a profile is answered locally. | Keeps working |
| `GatewayMode.LOCAL` | Answers the thermostat with its recorded profile. OJ's gateway is never contacted. | Shows the thermostat offline |

A WG4 thermostat needs a profile before it can be answered locally, so start
in relay mode. A WD5 thermostat needs none. The gateway sets the schedule and
manual modes of a WG4 thermostat, and the schedule, comfort, manual, boost and
eco modes of a WD5 thermostat. Other modes and the energy usage still go
through the cloud API, which only reaches a thermostat that is relayed.

A thermostat can take longer to acknowledge a command than the 10 seconds the
gateway waits. The call fails with `OJMicrolineTimeoutError` then, but when the
acknowledgement still comes, the new state is reported like any other change.

```python
import asyncio
import json
from pathlib import Path

from ojmicroline_thermostat import (
    DeviceProfile,
    GatewayMode,
    OJMicroline,
    Thermostat,
    WG4API,
    WG4Gateway,
    WG4LocalAPI,
    server_ssl_context,
)

STORE = Path("gateway.json")


def on_update(thermostat: Thermostat) -> None:
    print(f"{thermostat.name}: {thermostat.get_current_temperature() / 100}°C")


async def main():
    stored = json.loads(STORE.read_text()) if STORE.exists() else {}

    def save_snapshot(snapshot):
        stored["snapshot"] = snapshot
        STORE.write_text(json.dumps(stored))

    def save_profile(serial, profile):
        stored.setdefault("profiles", {})[serial] = profile.to_dict()
        STORE.write_text(json.dumps(stored))

    # The thermostat accepts any certificate, so a self-signed one will do:
    # openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    #     -subj /CN=na-thermos.ojelectronics.com \
    #     -keyout gateway.key -out gateway.crt
    context = await asyncio.to_thread(server_ssl_context, "gateway.crt", "gateway.key")
    gateway = WG4Gateway(
        context,
        mode=GatewayMode.RELAY,
        profiles={
            serial: DeviceProfile.from_dict(profile)
            for serial, profile in stored.get("profiles", {}).items()
        },
    )
    gateway.on_profile = save_profile
    api = WG4LocalAPI(
        WG4API(username="<your-username>", password="<your-password>"),
        gateway,
        snapshot=stored.get("snapshot"),
        on_snapshot=save_snapshot,
    )

    async with OJMicroline(api=api) as client:
        # The gateway only accepts the thermostats of the account, so fetch
        # them before it starts.
        await client.get_thermostats()
        await gateway.start()
        unsubscribe = client.subscribe(on_update)
        await asyncio.sleep(600)
        unsubscribe()
        await gateway.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

Three things are yours to store: the certificate, the profile of each
thermostat (`on_profile`) and the snapshot (`on_snapshot`). The snapshot is the
last answer of the cloud API, which keeps the thermostats usable when the cloud
cannot be reached. It holds the email address of the account.

Notifications come from the thermostats on the gateway only. A thermostat
that the cloud API serves, or a change made in the vendor's app while a
thermostat is not relayed, shows up in `get_thermostats()`, so keep calling
that now and then.

For the WD5 series the example is the same without the profiles:

```python
from ojmicroline_thermostat import WD5_R1099, WD5API, WD5Gateway, WD5LocalAPI

gateway = WD5Gateway(context, mode=GatewayMode.RELAY, endpoint=WD5_R1099)
api = WD5LocalAPI(
    WD5API(
        api_key="<app-api-key>",
        customer_id=99,
        username="<your-username>",
        password="<your-password>",
    ),
    gateway,
    snapshot=stored.get("snapshot"),
    on_snapshot=save_snapshot,
)
```

The cloud API of the WD5 series sets a mode for every thermostat of a group.
A command of the gateway only reaches the thermostat it is sent to.

Both gateways share these members:

| Member | Description |
| :----- | :---------- |
| `start()`, `stop()` | Start and stop listening. The port is that of the endpoint unless `listen_port` says otherwise. |
| `connected` | The serial numbers of the thermostats that are connected. |
| `is_relayed(serial)` | Whether a thermostat is connected to OJ's gateway as well. |
| `on_upstream`, `upstream_status` | Whether OJ's gateway can be used. `UpstreamStatus.UNTRUSTED` means its certificate does not match the pinned fingerprint; nothing is sent to it then. |
| `upstream_fingerprint` | The SHA-256 fingerprint of the certificate OJ's gateway showed last. |
| `history` | The last 300 events on the connections, each with its UTC time: connections and how TLS went, every message by field number and decoded temperature, commands and their answers. The same lines are logged at debug level by `ojmicroline_thermostat.gateway.server`. A WG4 login, which holds the email address of the account, is never in it. |

And both APIs these:

| Member | Description |
| :----- | :---------- |
| `cloud_error` | Why the cloud API could not be used when the thermostats were last fetched, or `None`. |
| `cloud` | The cloud API (`WG4API` or `WD5API`), for cloud-only calls such as `WG4API.set_energy_price`. |
| `on_gateway(serial)` | Whether the gateway serves a thermostat: it is connected now, or, in local mode, it was before. |
| `cloud_reaches(serial)` | Whether a change through the cloud API reaches a thermostat: it is not on the gateway, or it is relayed. |
| `can_set(serial, mode)` | Whether `set_regulation_mode()` can set a mode right now. |
| `cloud_thermostat(serial)` | A thermostat as the cloud API described it last, without what it reported to the gateway. |
| `local_modes` | The regulation modes the gateway sets by itself. |

Every device on your network can reach the gateway's port. The gateway only
serves the serial numbers of the account, but a serial number is not a secret:
run it on a network you trust.

The protocol between thermostat and gateway is described in
[docs/device-protocol.md](docs/device-protocol.md). That of the WG4 series was
worked out by
[peskyAdmin/schluter-local](https://github.com/peskyAdmin/schluter-local),
from which the gateway is adapted, and that of the WD5 series by
[patricknitsch/ioBroker.schlueter-thermostat](https://github.com/patricknitsch/ioBroker.schlueter-thermostat).

## Contributing

This is an active open-source project. We are always open to people who want to
use the code or contribute to it.

We've set up a separate document for our
[contribution guidelines](CONTRIBUTING.md).

Thank you for being involved! :heart_eyes:

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
[maintenance-shield]: https://img.shields.io/maintenance/yes/2026.svg
[maintainability-shield]: https://api.codeclimate.com/v1/badges/d77f7409eb02e331261b/maintainability
[maintainability-url]: https://codeclimate.com/github/robbinjanssen/python-ojmicroline-thermostat/maintainability
[project-stage-shield]: https://img.shields.io/badge/project%20stage-stable-brightgreen.svg
[pypi]: https://pypi.org/project/ojmicroline-thermostat/
[python-versions-shield]: https://img.shields.io/pypi/pyversions/ojmicroline-thermostat
[typing-shield]: https://github.com/robbinjanssen/python-ojmicroline-thermostat/actions/workflows/typing.yaml/badge.svg
[typing-url]: https://github.com/robbinjanssen/python-ojmicroline-thermostat/actions/workflows/typing.yaml
[releases-shield]: https://img.shields.io/github/release/robbinjanssen/python-ojmicroline-thermostat.svg
[releases]: https://github.com/robbinjanssen/python-ojmicroline-thermostat/releases
[stars-shield]: https://img.shields.io/github/stars/robbinjanssen/python-ojmicroline-thermostat.svg
[stars-url]: https://github.com/robbinjanssen/python-ojmicroline-thermostat/stargazers
