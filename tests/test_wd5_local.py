"""Test the API for WD5-series thermostats on a local gateway.

The cloud API is mocked with aresponses. The gateway is not started: the tests
report in the place of a thermostat, and stand in for its commands. What the
series share is tested in test_wg4_local.py.
"""

from __future__ import annotations

import json
import ssl
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest
from aresponses import Response, ResponsesMockServer

from ojmicroline_thermostat import (
    WD5API,
    GatewayMode,
    OJMicroline,
    OJMicrolineError,
    Thermostat,
    WD5Gateway,
    WD5LocalAPI,
    wd5_local,
)
from ojmicroline_thermostat.const import (
    REGULATION_BOOST,
    REGULATION_COMFORT,
    REGULATION_FROST_PROTECTION,
    REGULATION_MANUAL,
    REGULATION_SCHEDULE,
)

from . import load_fixtures

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

HOST = "ojmicroline.test.host"
# The two thermostats in the fixture of the cloud API. The first is the one
# that connects to the gateway in these tests.
SERIAL = "800747"
OTHER = "800857"
END = datetime(2026, 11, 14, 19, 0, tzinfo=UTC)

SnapshotData = dict[str, dict[str, Any]]


def _snapshot() -> SnapshotData:
    """Return the thermostats of the fixture as a stored snapshot."""
    groups = json.loads(load_fixtures("wd5_group.json"))["GroupContents"]
    return {
        item["SerialNumber"]: item
        for group in groups
        for item in group["Thermostats"]
        if item
    }


def _respond(aresponses: ResponsesMockServer, path: str, body: Any) -> None:
    """Let the cloud API answer the next request for a path with JSON."""
    method = "GET" if path == "/api/Group/GroupContents" else "POST"
    text = body if isinstance(body, str) else json.dumps(body)
    aresponses.add(
        HOST,
        path,
        method,
        Response(status=200, headers={"Content-Type": "application/json"}, text=text),
    )


def _report(gateway: WD5Gateway, serial: str, **fields: Any) -> None:
    """Let the gateway report something, as if a thermostat sent it.

    The thermostat counts as connected, answered locally, until it reports
    that it is offline.
    """
    assert gateway.on_state is not None
    links = gateway._links  # pylint: disable=protected-access
    if fields.get("Online") is False:
        links.pop(serial, None)
    else:
        links.setdefault(serial, Mock(upstream=None))
    gateway.on_state(serial, fields)


@pytest.fixture(name="session")
async def session_fixture() -> AsyncIterator[aiohttp.ClientSession]:
    """Provide the HTTP session for the cloud API."""
    async with aiohttp.ClientSession() as session:
        yield session


@pytest.fixture(name="gateway")
def gateway_fixture(monkeypatch: pytest.MonkeyPatch) -> WD5Gateway:
    """Create a gateway that is not started, and that takes every command."""
    gateway = WD5Gateway(
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER), mode=GatewayMode.RELAY
    )
    monkeypatch.setattr(gateway, "set_mode", AsyncMock())
    return gateway


@pytest.fixture(name="api")
def api_fixture(gateway: WD5Gateway, session: aiohttp.ClientSession) -> WD5LocalAPI:
    """Create the API with a stored snapshot, on a client with a session."""
    cloud = WD5API(
        host=HOST,
        api_key="ap1-k3y-v3ry-s3cret",
        customer_id=99,
        username="py",
        password="test",
    )
    api = WD5LocalAPI(cloud, gateway, snapshot=_snapshot())
    OJMicroline(api=api, session=session)
    return api


async def _thermostat(api: WD5LocalAPI, serial: str = SERIAL) -> Thermostat:
    """Return a thermostat as the API describes it, without asking the cloud."""
    thermostats = await api.get_notifications()
    return next(item for item in thermostats if item.serial_number == serial)


async def test_fetch_takes_the_snapshot_from_the_cloud(
    aresponses: ResponsesMockServer, api: WD5LocalAPI, gateway: WD5Gateway
) -> None:
    """The thermostats come from the cloud API, with what they report locally."""
    _respond(aresponses, "/api/UserProfile/SignIn", {"SessionId": "s1", "ErrorCode": 0})
    _respond(aresponses, "/api/Group/GroupContents", load_fixtures("wd5_group.json"))
    _report(gateway, SERIAL, Online=True, RoomTemperature=2175, RegulationMode=3)

    thermostats = {item.serial_number: item for item in await api.get_thermostats()}

    assert set(thermostats) == {SERIAL, OTHER}
    assert gateway.allowed_serials == {SERIAL, OTHER}
    assert api.cloud_error is None
    # The one on the gateway has what it reported; the other is the cloud's.
    assert thermostats[SERIAL].temperature_room == 2175
    assert thermostats[SERIAL].regulation_mode == REGULATION_MANUAL
    assert thermostats[SERIAL].online is True
    assert thermostats[OTHER].regulation_mode == REGULATION_FROST_PROTECTION
    assert api.on_gateway(SERIAL)
    assert not api.on_gateway(OTHER)


async def test_thermostat_that_left_the_gateway_is_offline(
    api: WD5LocalAPI, gateway: WD5Gateway
) -> None:
    """In local mode the cloud does not know whether a thermostat is here."""
    gateway.mode = GatewayMode.LOCAL
    _report(gateway, SERIAL, Online=True)
    _report(gateway, SERIAL, Online=False)
    assert (await _thermostat(api)).online is False
    assert api.on_gateway(SERIAL)
    assert not api.cloud_reaches(SERIAL)
    # A thermostat that never connected here is as the cloud describes it.
    assert (await _thermostat_again(api, OTHER)).online is True


async def test_relayed_thermostat_that_left_is_the_clouds(
    api: WD5LocalAPI, gateway: WD5Gateway
) -> None:
    """In relay mode a thermostat that left may talk to OJ's gateway itself.

    Its commands then go through the cloud, which reaches it that way.
    """
    _report(gateway, SERIAL, Online=True)
    assert api.on_gateway(SERIAL)
    _report(gateway, SERIAL, Online=False)
    assert not api.on_gateway(SERIAL)
    assert api.cloud_reaches(SERIAL)
    assert api.can_set(SERIAL, REGULATION_FROST_PROTECTION)


async def _thermostat_again(api: WD5LocalAPI, serial: str) -> Thermostat:
    """Return a thermostat from the snapshot, after the first notification."""
    # pylint: disable-next=protected-access
    return api._thermostat(serial)


async def test_local_only_offers_the_modes_of_the_gateway(
    api: WD5LocalAPI, gateway: WD5Gateway
) -> None:
    """Without the cloud a thermostat only has the modes the gateway can set."""
    gateway.mode = GatewayMode.LOCAL
    _report(gateway, SERIAL, Online=True)
    thermostat = await _thermostat(api)
    assert thermostat.supported_regulation_modes == list(wd5_local.LOCAL_MODES)


@pytest.mark.parametrize(
    ("mode", "temperature", "minutes"),
    [
        (REGULATION_SCHEDULE, None, None),
        (REGULATION_MANUAL, 2250, None),
        # A comfort hold lasts as long as asked, a boost an hour.
        (REGULATION_COMFORT, 2400, 90),
        (REGULATION_BOOST, None, 60),
    ],
)
async def test_local_modes_go_to_the_gateway(  # noqa: PLR0913, PLR0917 # pylint: disable=too-many-arguments
    api: WD5LocalAPI,
    gateway: WD5Gateway,
    monkeypatch: pytest.MonkeyPatch,
    mode: int,
    temperature: int | None,
    minutes: int | None,
) -> None:
    """A mode the gateway can set goes straight to the thermostat."""
    asked: list[tuple[int, int]] = []

    def local_in(length: int, time_zone: int) -> datetime:
        asked.append((length, time_zone))
        return END

    monkeypatch.setattr(wd5_local, "local_in", local_in)
    _report(gateway, SERIAL, Online=True, ManualModeSetpoint=2300)
    thermostat = await _thermostat(api)

    assert await api.set_regulation_mode(thermostat, mode, temperature, 90)

    set_mode = gateway.set_mode
    assert isinstance(set_mode, AsyncMock)
    set_mode.assert_awaited_once()
    arguments = set_mode.await_args
    assert arguments is not None
    assert arguments.args == (SERIAL, mode, temperature)
    # The end is in the time zone the cloud API gives for the thermostat.
    assert arguments.kwargs["end"] == (None if minutes is None else END)
    assert asked == ([] if minutes is None else [(minutes, 3600)])
    # What is known: the snapshot, and over it what the thermostat reported.
    known = arguments.kwargs["known"]
    assert known["ComfortSetpoint"] == 2000
    assert known["ManualModeSetpoint"] == 2300


async def test_other_modes_go_through_the_cloud(
    aresponses: ResponsesMockServer,
    api: WD5LocalAPI,
    gateway: WD5Gateway,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Frost protection is set in the cloud, which needs the relay."""
    _report(gateway, SERIAL, Online=True)
    thermostat = await _thermostat(api)

    with pytest.raises(OJMicrolineError, match="not connected"):
        await api.set_regulation_mode(thermostat, REGULATION_FROST_PROTECTION, None, 60)

    monkeypatch.setattr(gateway, "is_relayed", lambda _serial: True)
    _respond(aresponses, "/api/UserProfile/SignIn", {"SessionId": "s1", "ErrorCode": 0})
    _respond(aresponses, "/api/Group/UpdateGroup", {"ErrorCode": 0})
    assert await api.set_regulation_mode(
        thermostat, REGULATION_FROST_PROTECTION, None, 60
    )
    set_mode = gateway.set_mode
    assert isinstance(set_mode, AsyncMock)
    set_mode.assert_not_awaited()


async def test_temperature_outside_the_limits_is_refused(
    api: WD5LocalAPI, gateway: WD5Gateway
) -> None:
    """The thermostat does not check a setpoint, so the API does."""
    _report(gateway, SERIAL, Online=True)
    thermostat = await _thermostat(api)
    for mode in (REGULATION_COMFORT, REGULATION_MANUAL):
        with pytest.raises(OJMicrolineError, match="outside the limits"):
            await api.set_regulation_mode(thermostat, mode, 9000, 60)
        with pytest.raises(OJMicrolineError, match="outside the limits"):
            await api.set_regulation_mode(thermostat, mode, -500, 60)
    set_mode = gateway.set_mode
    assert isinstance(set_mode, AsyncMock)
    set_mode.assert_not_awaited()


@pytest.mark.parametrize(
    ("time_zone", "error"),
    [(None, "not known"), ("3600", "not known"), (86400, "impossible time zone")],
)
async def test_end_needs_the_time_zone(
    api: WD5LocalAPI, gateway: WD5Gateway, time_zone: Any, error: str
) -> None:
    """A comfort hold or a boost cannot end without the time zone."""
    _report(gateway, SERIAL, Online=True)
    thermostat = await _thermostat(api)
    _report(gateway, SERIAL, TimeZone=time_zone)
    for mode in (REGULATION_COMFORT, REGULATION_BOOST):
        with pytest.raises(OJMicrolineError, match=error):
            await api.set_regulation_mode(thermostat, mode, None, 60)


async def test_cloud_thermostat_leaves_out_what_it_reported(
    api: WD5LocalAPI, gateway: WD5Gateway
) -> None:
    """A group change starts from the cloud's view of the thermostat."""
    _report(gateway, SERIAL, Online=True, RegulationMode=REGULATION_COMFORT)
    assert (await _thermostat(api)).regulation_mode == REGULATION_COMFORT
    cloud = api.cloud_thermostat(SERIAL)
    assert cloud is not None
    assert cloud.regulation_mode == REGULATION_SCHEDULE
    assert api.cloud_thermostat("0") is None
