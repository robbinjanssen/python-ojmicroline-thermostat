"""Test the WG4LocalAPI class.

Most tests use a gateway that is not started, and let it report as if a
thermostat were connected. The last test runs everything for real: a fake
thermostat on a started gateway, and no cloud at all.
"""

from __future__ import annotations

import asyncio
import json
import ssl
from datetime import time
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest
from aresponses import Response, ResponsesMockServer

from ojmicroline_thermostat import (
    WG4API,
    GatewayMode,
    OJMicroline,
    OJMicrolineAuthError,
    OJMicrolineConnectionError,
    OJMicrolineError,
    OJMicrolineResultsError,
    OJMicrolineScheduleError,
    OJMicrolineTimeoutError,
    Thermostat,
    WG4Gateway,
    WG4LocalAPI,
    server_ssl_context,
)
from ojmicroline_thermostat import local as local_api
from ojmicroline_thermostat.const import (
    REGULATION_COMFORT,
    REGULATION_MANUAL,
    REGULATION_SCHEDULE,
)

from . import load_fixtures
from .gateway_fakes import PROFILE, FakeThermostat, acknowledgement

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

HOST = "ojmicroline.test.host"
# The two thermostats in the fixture of the cloud API. The first is the one
# that connects to the gateway in these tests.
SERIAL = "42424242"
OTHER = "42424243"

SnapshotData = dict[str, dict[str, Any]]


def _snapshot() -> SnapshotData:
    """Return the thermostats of the fixture as a stored snapshot."""
    groups = json.loads(load_fixtures("wg4_group.json"))["Groups"]
    return {
        item["SerialNumber"]: item
        for group in groups
        for item in group["Thermostats"]
        if item
    }


def _respond(aresponses: ResponsesMockServer, path: str, body: Any) -> None:
    """Let the cloud API answer the next request for a path with JSON."""
    method = "GET" if path in {"/api/thermostats", "/api/energyusage"} else "POST"
    text = body if isinstance(body, str) else json.dumps(body)
    aresponses.add(
        HOST,
        path,
        method,
        Response(status=200, headers={"Content-Type": "application/json"}, text=text),
    )


def _cloud_answers(aresponses: ResponsesMockServer) -> None:
    """Let the cloud API accept a login and return the thermostats once."""
    _respond(aresponses, "/api/authenticate/user", {"SessionId": "s1", "ErrorCode": 0})
    _respond(aresponses, "/api/thermostats", load_fixtures("wg4_group.json"))


def _cloud_fails(aresponses: ResponsesMockServer) -> None:
    """Let the cloud API answer a login with a server error."""
    aresponses.add(HOST, "/api/authenticate/user", "POST", Response(status=500))


def _report(gateway: WG4Gateway, serial: str, **fields: Any) -> None:
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


@pytest.fixture(name="cloud")
def cloud_fixture() -> WG4API:
    """Create the cloud API of the account."""
    return WG4API(host=HOST, username="py", password="test")


@pytest.fixture(name="gateway")
def gateway_fixture() -> WG4Gateway:
    """Create a gateway that is not started; the tests report in its place."""
    return WG4Gateway(ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER), mode=GatewayMode.RELAY)


def _client(
    api: WG4LocalAPI, session: aiohttp.ClientSession
) -> tuple[OJMicroline, WG4LocalAPI]:
    return OJMicroline(api=api, session=session), api


def _by_serial(thermostats: list[Thermostat]) -> dict[str, Thermostat]:
    return {thermostat.serial_number: thermostat for thermostat in thermostats}


async def test_first_fetch_takes_the_snapshot_from_the_cloud(
    aresponses: ResponsesMockServer,
    session: aiohttp.ClientSession,
    cloud: WG4API,
    gateway: WG4Gateway,
) -> None:
    """Without a stored snapshot the thermostats come from the cloud API."""
    stored: list[SnapshotData] = []
    client, api = _client(
        WG4LocalAPI(cloud, gateway, on_snapshot=stored.append), session
    )
    assert not gateway.allowed_serials
    _cloud_answers(aresponses)

    thermostats = _by_serial(await client.get_thermostats())

    assert set(thermostats) == {SERIAL, OTHER}
    # Neither connected to the gateway, so both are as the cloud describes them.
    assert thermostats[SERIAL].online is True
    assert thermostats[SERIAL].name == "RoomName"
    assert thermostats[SERIAL].energy is None
    assert api.cloud_error is None
    # The thermostats of the account may connect, and the snapshot is stored.
    assert gateway.allowed_serials == {SERIAL, OTHER}
    assert stored == [_snapshot()]


async def test_stored_snapshot_survives_a_cloud_failure(
    aresponses: ResponsesMockServer,
    session: aiohttp.ClientSession,
    cloud: WG4API,
    gateway: WG4Gateway,
) -> None:
    """With a stored snapshot the thermostats are there without the cloud."""
    stored: list[SnapshotData] = []
    client, api = _client(
        WG4LocalAPI(cloud, gateway, snapshot=_snapshot(), on_snapshot=stored.append),
        session,
    )
    # Thermostats can connect before the cloud was asked anything.
    assert gateway.allowed_serials == {SERIAL, OTHER}
    _cloud_fails(aresponses)

    thermostats = _by_serial(await client.get_thermostats())

    assert set(thermostats) == {SERIAL, OTHER}
    assert isinstance(api.cloud_error, OJMicrolineConnectionError)
    assert not stored

    # Once the cloud answers again, the error is gone. The answer equals the
    # stored snapshot, so there is nothing new to store.
    _cloud_answers(aresponses)
    await client.get_thermostats()
    assert api.cloud_error is None
    assert not stored


async def test_first_fetch_fails_without_a_snapshot(
    aresponses: ResponsesMockServer,
    session: aiohttp.ClientSession,
    cloud: WG4API,
    gateway: WG4Gateway,
) -> None:
    """Without a snapshot there is nothing to fall back on."""
    client, api = _client(WG4LocalAPI(cloud, gateway), session)

    _cloud_fails(aresponses)
    with pytest.raises(OJMicrolineConnectionError):
        await client.get_thermostats()
    assert isinstance(api.cloud_error, OJMicrolineConnectionError)

    # A rejected password is reported as such, so the user can be asked for it.
    _respond(aresponses, "/api/authenticate/user", {"ErrorCode": 1})
    with pytest.raises(OJMicrolineAuthError):
        await client.get_thermostats()
    assert isinstance(api.cloud_error, OJMicrolineAuthError)


async def test_slow_cloud_is_not_waited_for(
    session: aiohttp.ClientSession,
    cloud: WG4API,
    gateway: WG4Gateway,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cloud API that takes too long counts as a cloud that failed."""
    monkeypatch.setattr(local_api, "CLOUD_TIMEOUT", 0.05)

    async def slow() -> None:
        await asyncio.sleep(1)

    monkeypatch.setattr(cloud, "login", slow)

    client, api = _client(WG4LocalAPI(cloud, gateway), session)
    with pytest.raises(OJMicrolineTimeoutError, match="did not answer in time"):
        await client.get_thermostats()

    client, api = _client(WG4LocalAPI(cloud, gateway, snapshot=_snapshot()), session)
    assert len(await client.get_thermostats()) == 2
    assert isinstance(api.cloud_error, OJMicrolineTimeoutError)


async def test_expired_cloud_session_is_renewed(
    aresponses: ResponsesMockServer,
    session: aiohttp.ClientSession,
    cloud: WG4API,
    gateway: WG4Gateway,
) -> None:
    """A session the cloud API no longer accepts is replaced by a new one."""
    client, api = _client(WG4LocalAPI(cloud, gateway), session)
    _respond(aresponses, "/api/authenticate/user", {"SessionId": "old", "ErrorCode": 0})
    aresponses.add(HOST, "/api/thermostats", "GET", Response(status=401))
    _respond(aresponses, "/api/authenticate/user", {"SessionId": "new", "ErrorCode": 0})
    _respond(aresponses, "/api/thermostats", load_fixtures("wg4_group.json"))

    assert len(await client.get_thermostats()) == 2
    assert api.cloud_error is None
    aresponses.assert_plan_strictly_followed()


async def test_empty_answer_keeps_the_snapshot(
    aresponses: ResponsesMockServer,
    session: aiohttp.ClientSession,
    cloud: WG4API,
    gateway: WG4Gateway,
) -> None:
    """A cloud that suddenly knows no thermostats does not lock them out."""
    stored: list[SnapshotData] = []
    client, api = _client(
        WG4LocalAPI(cloud, gateway, snapshot=_snapshot(), on_snapshot=stored.append),
        session,
    )
    _respond(aresponses, "/api/authenticate/user", {"SessionId": "s1", "ErrorCode": 0})
    _respond(aresponses, "/api/thermostats", {"Groups": []})

    assert len(await client.get_thermostats()) == 2
    assert api.cloud_error is None
    assert gateway.allowed_serials == {SERIAL, OTHER}
    assert not stored


async def test_new_snapshot_is_stored(
    aresponses: ResponsesMockServer,
    session: aiohttp.ClientSession,
    cloud: WG4API,
    gateway: WG4Gateway,
) -> None:
    """A change in the cloud replaces the snapshot, with or without a listener."""
    # A thermostat that was added to the account since the snapshot was stored.
    old = {SERIAL: _snapshot()[SERIAL]}
    client, _ = _client(WG4LocalAPI(cloud, gateway, snapshot=old), session)
    assert gateway.allowed_serials == {SERIAL}
    _cloud_answers(aresponses)

    assert len(await client.get_thermostats()) == 2
    assert gateway.allowed_serials == {SERIAL, OTHER}


async def test_thermostat_on_the_gateway_reports_for_itself(
    aresponses: ResponsesMockServer,
    session: aiohttp.ClientSession,
    cloud: WG4API,
    gateway: WG4Gateway,
) -> None:
    """What a thermostat reports to the gateway goes over the cloud's values."""
    client, _ = _client(WG4LocalAPI(cloud, gateway, snapshot=_snapshot()), session)
    _report(gateway, SERIAL, Online=True)
    _report(gateway, SERIAL, RegulationMode=1, ManualTemperature=2100, Room="Hall")
    _report(gateway, SERIAL, Temperature=1950, Heating=True)
    _cloud_answers(aresponses)

    thermostats = _by_serial(await client.get_thermostats())

    local = thermostats[SERIAL]
    assert local.online is True
    assert local.name == "Hall"
    assert local.regulation_mode == REGULATION_SCHEDULE
    assert local.manual_temperature == 2100
    assert local.temperature == 1950
    assert local.heating is True
    # What it does not report still comes from the cloud API.
    assert local.max_temperature == 4000
    assert local.software_version == "1012M203"
    # The other thermostat is untouched.
    assert thermostats[OTHER].name == "SecondRoom"
    assert thermostats[OTHER].temperature == 2001


async def test_temperature_is_not_known_until_the_thermostat_measured_it(
    session: aiohttp.ClientSession, cloud: WG4API, gateway: WG4Gateway
) -> None:
    """What the cloud measured last is not what a thermostat here measures.

    The cloud does not hear from a thermostat in local mode. Right after it
    connected, a thermostat has not reported its temperature yet.
    """
    _, api = _client(WG4LocalAPI(cloud, gateway, snapshot=_snapshot()), session)
    _report(gateway, SERIAL, Online=True, RegulationMode=1)

    thermostats = _by_serial(await api.get_notifications())
    assert thermostats[SERIAL].online is True
    assert thermostats[SERIAL].temperature is None
    # A thermostat the cloud serves keeps what the cloud measured.
    assert thermostats[OTHER].temperature == 2001

    _report(gateway, SERIAL, Temperature=1950)
    assert _by_serial(await api.get_notifications())[SERIAL].temperature == 1950


async def test_known_thermostat_is_offline_until_it_connects(
    aresponses: ResponsesMockServer,
    session: aiohttp.ClientSession,
    cloud: WG4API,
    gateway: WG4Gateway,
) -> None:
    """In local mode, a thermostat with a profile is online when it connects.

    The cloud calls it online in its snapshot, but the cloud cannot know
    whether a thermostat is connected to this gateway.
    """
    gateway.profiles[SERIAL] = PROFILE
    client, _ = _client(WG4LocalAPI(cloud, gateway, snapshot=_snapshot()), session)
    # In relay mode a thermostat that is not connected may talk to OJ's
    # gateway directly, so the cloud's word holds.
    _cloud_fails(aresponses)
    assert _by_serial(await client.get_thermostats())[SERIAL].online is True

    gateway.mode = GatewayMode.LOCAL
    _cloud_fails(aresponses)
    thermostats = _by_serial(await client.get_thermostats())
    assert thermostats[SERIAL].online is False
    assert thermostats[OTHER].online is True

    _report(gateway, SERIAL, Online=True)
    _cloud_fails(aresponses)
    assert _by_serial(await client.get_thermostats())[SERIAL].online is True

    _report(gateway, SERIAL, Online=False)
    _cloud_fails(aresponses)
    assert _by_serial(await client.get_thermostats())[SERIAL].online is False


async def test_supported_regulation_modes(
    aresponses: ResponsesMockServer,
    session: aiohttp.ClientSession,
    cloud: WG4API,
    gateway: WG4Gateway,
) -> None:
    """Without the cloud, a thermostat on the gateway has two modes."""
    cloud_modes = [REGULATION_SCHEDULE, REGULATION_COMFORT, REGULATION_MANUAL]
    client, api = _client(WG4LocalAPI(cloud, gateway, snapshot=_snapshot()), session)
    _report(gateway, SERIAL, Online=True)
    link = gateway._links[SERIAL]  # pylint: disable=protected-access

    # While it is relayed, the cloud sets the modes the gateway cannot.
    link.upstream = Mock()
    _cloud_fails(aresponses)
    thermostats = _by_serial(await client.get_thermostats())
    assert thermostats[SERIAL].supported_regulation_modes == cloud_modes
    assert api.can_set(SERIAL, REGULATION_COMFORT)

    # Answered locally, because OJ's gateway cannot be used, or in local mode.
    link.upstream = None
    _cloud_fails(aresponses)
    thermostats = _by_serial(await client.get_thermostats())
    assert thermostats[SERIAL].supported_regulation_modes == [
        REGULATION_SCHEDULE,
        REGULATION_MANUAL,
    ]
    assert not api.can_set(SERIAL, REGULATION_COMFORT)
    assert api.can_set(SERIAL, REGULATION_MANUAL)
    assert not api.cloud_reaches(SERIAL)
    assert api.cloud_reaches(OTHER)

    gateway.mode = GatewayMode.LOCAL
    _cloud_fails(aresponses)
    thermostats = _by_serial(await client.get_thermostats())
    assert thermostats[SERIAL].supported_regulation_modes == [
        REGULATION_SCHEDULE,
        REGULATION_MANUAL,
    ]
    # A thermostat that is not on the gateway is still set through the cloud.
    assert thermostats[OTHER].supported_regulation_modes == cloud_modes


async def test_notifications(
    session: aiohttp.ClientSession, cloud: WG4API, gateway: WG4Gateway
) -> None:
    """A notification is what the thermostats reported since the last one."""
    _, api = _client(WG4LocalAPI(cloud, gateway, snapshot=_snapshot()), session)
    _report(gateway, SERIAL, Online=True)

    # The first call returns every thermostat, as a subscription expects.
    first = _by_serial(await api.get_notifications())
    assert set(first) == {SERIAL, OTHER}
    assert first[SERIAL].online is True

    # After that a call waits for a report.
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await api.get_notifications()

    # Several reports of one thermostat arrive as one notification.
    _report(gateway, SERIAL, Temperature=1950)
    _report(gateway, SERIAL, Heating=True)
    changed = await api.get_notifications()
    assert [thermostat.serial_number for thermostat in changed] == [SERIAL]
    assert changed[0].temperature == 1950
    assert changed[0].heating is True

    # A report about a thermostat that is not on the account is no news.
    _report(gateway, "7654321", Online=True)
    assert await api.get_notifications() == []


async def test_new_subscriber_gets_every_thermostat(
    session: aiohttp.ClientSession, cloud: WG4API, gateway: WG4Gateway
) -> None:
    """Another task that waits for notifications starts with every thermostat.

    OJMicroline starts a new task when it subscribes again, which has to
    catch up on what it missed.
    """
    _, api = _client(WG4LocalAPI(cloud, gateway, snapshot=_snapshot()), session)
    assert len(await asyncio.create_task(api.get_notifications())) == 2
    assert len(await asyncio.create_task(api.get_notifications())) == 2


async def test_thermostat_that_cannot_be_read_is_skipped(
    session: aiohttp.ClientSession,
    cloud: WG4API,
    gateway: WG4Gateway,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One thermostat that cannot be read does not hold up the others."""
    snapshot = _snapshot()
    del snapshot[OTHER]["SerialNumber"]
    _, api = _client(WG4LocalAPI(cloud, gateway, snapshot=snapshot), session)
    thermostats = await api.get_notifications()
    assert [thermostat.serial_number for thermostat in thermostats] == [SERIAL]
    assert f"The data of thermostat {OTHER} cannot be read" in caplog.text


async def test_set_regulation_mode_on_the_gateway(
    session: aiohttp.ClientSession, cloud: WG4API, gateway: WG4Gateway
) -> None:
    """The schedule and manual modes go to the thermostat, without the cloud."""
    client, api = _client(WG4LocalAPI(cloud, gateway, snapshot=_snapshot()), session)
    _report(gateway, SERIAL, Online=True)
    thermostat = _by_serial(await api.get_notifications())[SERIAL]
    send = AsyncMock()
    gateway.set_mode = send  # type: ignore[method-assign]

    assert await client.set_regulation_mode(thermostat, REGULATION_MANUAL, 2500)
    send.assert_awaited_once_with(SERIAL, REGULATION_MANUAL, 2500)

    send.reset_mock()
    assert await client.set_regulation_mode(thermostat, REGULATION_SCHEDULE)
    send.assert_awaited_once_with(SERIAL, REGULATION_SCHEDULE, None)

    # A temperature outside the limits of the thermostat is not sent.
    send.reset_mock()
    with pytest.raises(OJMicrolineError, match="outside the limits"):
        await client.set_regulation_mode(thermostat, REGULATION_MANUAL, 9000)
    send.assert_not_awaited()

    # An error of the gateway, such as a thermostat that is not connected,
    # reaches the caller.
    send.side_effect = OJMicrolineConnectionError("not connected")
    with pytest.raises(OJMicrolineConnectionError, match="not connected"):
        await client.set_regulation_mode(thermostat, REGULATION_MANUAL, 2500)


async def test_set_regulation_mode_through_the_cloud(
    aresponses: ResponsesMockServer,
    session: aiohttp.ClientSession,
    cloud: WG4API,
    gateway: WG4Gateway,
) -> None:
    """A mode the gateway cannot set needs a thermostat that is relayed."""
    client, api = _client(WG4LocalAPI(cloud, gateway, snapshot=_snapshot()), session)
    _report(gateway, SERIAL, Online=True)
    thermostat = _by_serial(await api.get_notifications())[SERIAL]
    send = AsyncMock()
    gateway.set_mode = send  # type: ignore[method-assign]

    # Not relayed: the cloud cannot reach the thermostat, so it is not asked.
    gateway.is_relayed = Mock(return_value=False)  # type: ignore[method-assign]
    with pytest.raises(OJMicrolineError, match="set through the cloud"):
        await client.set_regulation_mode(thermostat, REGULATION_COMFORT, 2500, 60)

    # Relayed: the cloud API sets the mode, and the thermostat hears it from
    # OJ's gateway.
    gateway.is_relayed = Mock(return_value=True)  # type: ignore[method-assign]
    _respond(aresponses, "/api/authenticate/user", {"SessionId": "s1", "ErrorCode": 0})
    _respond(aresponses, "/api/thermostat", {"Success": True})
    assert await client.set_regulation_mode(thermostat, REGULATION_COMFORT, 2500, 60)

    send.assert_not_awaited()
    aresponses.assert_plan_strictly_followed()


async def test_thermostat_elsewhere_is_set_through_the_cloud(
    aresponses: ResponsesMockServer,
    session: aiohttp.ClientSession,
    cloud: WG4API,
    gateway: WG4Gateway,
) -> None:
    """A thermostat that never connected to the gateway is the cloud's."""
    client, api = _client(WG4LocalAPI(cloud, gateway, snapshot=_snapshot()), session)
    thermostat = _by_serial(await api.get_notifications())[OTHER]
    send = AsyncMock()
    gateway.set_mode = send  # type: ignore[method-assign]
    _respond(aresponses, "/api/authenticate/user", {"SessionId": "s1", "ErrorCode": 0})
    _respond(aresponses, "/api/thermostat", {"Success": True})

    assert await client.set_regulation_mode(thermostat, REGULATION_MANUAL, 2500)

    send.assert_not_awaited()
    aresponses.assert_plan_strictly_followed()


async def test_get_energy_usage(
    aresponses: ResponsesMockServer,
    session: aiohttp.ClientSession,
    cloud: WG4API,
    gateway: WG4Gateway,
) -> None:
    """Energy usage is something only the cloud API knows."""
    client, api = _client(WG4LocalAPI(cloud, gateway, snapshot=_snapshot()), session)
    thermostat = _by_serial(await api.get_notifications())[SERIAL]
    _respond(aresponses, "/api/authenticate/user", {"SessionId": "s1", "ErrorCode": 0})
    _respond(aresponses, "/api/energyusage", load_fixtures("wg4_energy.json"))

    usage = await client.get_energy_usage(thermostat)

    assert len(usage) == 7
    aresponses.assert_plan_strictly_followed()


async def test_expired_session_is_renewed_once(
    aresponses: ResponsesMockServer,
    session: aiohttp.ClientSession,
    cloud: WG4API,
    gateway: WG4Gateway,
) -> None:
    """OJMicroline renews an expired session, as it does for every API."""
    client, api = _client(WG4LocalAPI(cloud, gateway, snapshot=_snapshot()), session)
    thermostat = _by_serial(await api.get_notifications())[SERIAL]
    _respond(aresponses, "/api/authenticate/user", {"SessionId": "s1", "ErrorCode": 0})
    aresponses.add(HOST, "/api/energyusage", "GET", Response(status=401))
    _respond(aresponses, "/api/authenticate/user", {"SessionId": "s2", "ErrorCode": 0})
    _respond(aresponses, "/api/energyusage", load_fixtures("wg4_energy.json"))

    assert len(await client.get_energy_usage(thermostat)) == 7
    aresponses.assert_plan_strictly_followed()


async def test_session_belongs_to_the_cloud_api(
    aresponses: ResponsesMockServer,
    session: aiohttp.ClientSession,
    cloud: WG4API,
    gateway: WG4Gateway,
) -> None:
    """Logging in does nothing here; the cloud API has the session."""
    client, api = _client(WG4LocalAPI(cloud, gateway, snapshot=_snapshot()), session)
    assert api.host == HOST
    assert api.supports_notifications is True
    # The client gave the API its request function, which the cloud API uses.
    assert api.request == cloud.request

    # No request is made: the cloud API would refuse it, as nothing is planned.
    await client.login()
    aresponses.assert_plan_strictly_followed()

    _cloud_answers(aresponses)
    await client.get_thermostats()
    assert cloud._session_id == "s1"  # pylint: disable=protected-access
    api.invalidate_session()
    assert cloud._session_id is None  # pylint: disable=protected-access


async def test_thermostat_is_controlled_without_the_cloud(
    aresponses: ResponsesMockServer,
    session: aiohttp.ClientSession,
    cloud: WG4API,
    certificate: tuple[Path, Path],
) -> None:
    """A thermostat on a real gateway reports and is set with no cloud at all.

    Nothing is planned for the cloud API, so any request to it would fail.
    """
    gateway = WG4Gateway(
        server_ssl_context(*certificate),
        mode=GatewayMode.LOCAL,
        profiles={SERIAL: PROFILE},
        listen_host="127.0.0.1",
        listen_port=0,
    )
    client, _ = _client(WG4LocalAPI(cloud, gateway, snapshot=_snapshot()), session)
    updates: asyncio.Queue[Thermostat] = asyncio.Queue()
    unsubscribe = client.subscribe(updates.put_nowait)
    await gateway.start()
    thermostat = await FakeThermostat.connect(gateway.port)
    try:
        # The subscription starts with every thermostat of the account.
        async with asyncio.timeout(2):
            assert {(await updates.get()).serial_number for _ in range(2)} == {
                SERIAL,
                OTHER,
            }

        await thermostat.login(SERIAL)
        await thermostat.upload()

        # The upload arrives as the thermostat's own values.
        async with asyncio.timeout(2):
            update = await updates.get()
            while update.manual_temperature != 2222:
                update = await updates.get()
        assert update.serial_number == SERIAL
        assert update.online is True
        assert update.name == "Kitchen Floor"
        assert update.regulation_mode == REGULATION_MANUAL
        assert update.supported_regulation_modes == [
            REGULATION_SCHEDULE,
            REGULATION_MANUAL,
        ]

        # A new setpoint goes to the thermostat, which confirms it.
        task = asyncio.create_task(
            client.set_regulation_mode(update, REGULATION_MANUAL, 2500)
        )
        sent = await thermostat.receive()
        assert sent.varint(1) == 101
        await thermostat.send(acknowledgement(101))
        assert await task

        async with asyncio.timeout(2):
            update = await updates.get()
            while update.manual_temperature != 2500:
                update = await updates.get()
        assert update.get_target_temperature() == 2500
        aresponses.assert_plan_strictly_followed()
    finally:
        unsubscribe()
        await thermostat.close()
        await gateway.stop()
        await client.close()


async def test_snapshot_is_stored_when_something_lasting_changes(
    aresponses: ResponsesMockServer,
    session: aiohttp.ClientSession,
    cloud: WG4API,
    gateway: WG4Gateway,
) -> None:
    """A measured temperature alone is not worth writing the snapshot again."""
    stored: list[SnapshotData] = []
    client, _ = _client(
        WG4LocalAPI(cloud, gateway, snapshot=_snapshot(), on_snapshot=stored.append),
        session,
    )
    answer = json.loads(load_fixtures("wg4_group.json"))
    measured = next(
        item
        for group in answer["Groups"]
        for item in group["Thermostats"]
        if item.get("SerialNumber") == OTHER
    )
    measured.update(Temperature=1234, Heating=not measured["Heating"], Online=False)
    _respond(aresponses, "/api/authenticate/user", {"SessionId": "s1", "ErrorCode": 0})
    _respond(aresponses, "/api/thermostats", answer)

    thermostats = _by_serial(await client.get_thermostats())
    # The new values are used, and nothing is stored.
    assert thermostats[measured["SerialNumber"]].temperature == 1234
    assert not stored

    measured["ManualTemperature"] = 2345
    _respond(aresponses, "/api/thermostats", answer)
    await client.get_thermostats()
    assert len(stored) == 1
    assert stored[0][measured["SerialNumber"]]["ManualTemperature"] == 2345


async def test_answer_in_another_form_keeps_the_snapshot(
    aresponses: ResponsesMockServer,
    session: aiohttp.ClientSession,
    cloud: WG4API,
    gateway: WG4Gateway,
) -> None:
    """A cloud that answers something else must not stop the thermostats."""
    stored: list[SnapshotData] = []
    client, api = _client(
        WG4LocalAPI(cloud, gateway, snapshot=_snapshot(), on_snapshot=stored.append),
        session,
    )
    _respond(aresponses, "/api/authenticate/user", {"SessionId": "s1", "ErrorCode": 0})
    _respond(aresponses, "/api/thermostats", {"ErrorCode": 0, "Message": "Try later"})

    assert len(await client.get_thermostats()) == 2
    assert isinstance(api.cloud_error, OJMicrolineResultsError)

    # Not the thermostats at all, one without a serial number, and one that
    # has a serial number and nothing else: that one must not replace the
    # snapshot either, or it would be stored and never read again.
    answers: list[Any] = [
        [],
        {"Groups": [{"Thermostats": [{"Room": "No serial"}]}]},
        {"Groups": [{"Thermostats": [{"SerialNumber": SERIAL}]}]},
    ]
    for odd in answers:
        _respond(aresponses, "/api/thermostats", odd)
        assert len(await client.get_thermostats()) == 2
        assert isinstance(api.cloud_error, OJMicrolineResultsError)
        # The answer stays out of the error, which ends up in logs.
        assert len(api.cloud_error.args) == 1
    assert not stored


async def test_energy_price_is_remembered(
    aresponses: ResponsesMockServer,
    session: aiohttp.ClientSession,
    cloud: WG4API,
    gateway: WG4Gateway,
) -> None:
    """A new price outlives the next report of the thermostat.

    The thermostat does not report the price, so without remembering it the
    old one of the snapshot would come back.
    """
    stored: list[SnapshotData] = []
    _, api = _client(
        WG4LocalAPI(cloud, gateway, snapshot=_snapshot(), on_snapshot=stored.append),
        session,
    )
    thermostat = _by_serial(await api.get_notifications())[SERIAL]
    _respond(aresponses, "/api/authenticate/user", {"SessionId": "s1", "ErrorCode": 0})
    _respond(aresponses, "/api/thermostat", {"Success": True})
    _respond(aresponses, "/api/thermostat", {"Success": True})

    assert await api.set_energy_price(thermostat, 0.314)

    _report(gateway, SERIAL, Online=True, Temperature=2100)
    assert _by_serial(await api.get_notifications())[SERIAL].energy_price == 0.31
    # The price is stored as well, or a restart would bring the old one back.
    assert [snapshot[SERIAL]["KwhCharge"] for snapshot in stored] == [0.31]

    # The same price again is nothing new to store.
    assert await api.set_energy_price(thermostat, 0.31)
    assert len(stored) == 1
    aresponses.assert_plan_strictly_followed()


async def test_usage_is_counted_with_the_load_of_the_cloud(
    cloud: WG4API, gateway: WG4Gateway
) -> None:
    """A WG4 thermostat is counted with the load the cloud API tells."""
    used: list[tuple[str, float]] = []
    WG4LocalAPI(
        cloud,
        gateway,
        snapshot=_snapshot(),
        on_usage=lambda serial, energy: used.append((serial, energy)),
    )

    # The fixture has a measured load of 1218 W for this thermostat.
    _report(gateway, SERIAL, Online=True, Temperature=2100, Heating=True)
    assert used == [(SERIAL, pytest.approx(1218 * 60 / 3_600_000))]


async def test_schedule_from_the_cloud_and_the_thermostat(
    cloud: WG4API, gateway: WG4Gateway
) -> None:
    """The schedule comes from the cloud, until the thermostat reports its own."""
    api = WG4LocalAPI(cloud, gateway, snapshot=_snapshot())
    thermostats = _by_serial(await api.get_notifications())
    schedule = thermostats[SERIAL].schedule
    assert schedule is not None
    assert schedule["Days"][0]["Events"][0]["Clock"] == "06:00:00"

    reported = [
        {
            "WeekDayGrpNo": number,
            "Events": [
                {
                    "ScheduleType": 0,
                    "Clock": "07:15:00",
                    "TempFloor": 2050,
                    "Active": True,
                }
            ],
        }
        for number in range(1, 8)
    ]
    _report(gateway, SERIAL, Online=True, Schedules=reported)
    thermostats = _by_serial(await api.get_notifications())
    schedule = thermostats[SERIAL].schedule
    assert schedule is not None
    assert schedule["Days"][6]["Events"] == [
        {
            "ScheduleType": 0,
            "Clock": "07:15:00",
            "Temperature": 2050,
            "Active": True,
            "EventIsOnNextDay": False,
        }
    ]


async def test_set_schedule(cloud: WG4API, gateway: WG4Gateway) -> None:
    """A schedule goes to a connected thermostat, once it fits the rules."""
    api = WG4LocalAPI(cloud, gateway, snapshot=_snapshot())
    _report(gateway, SERIAL, Online=True)
    thermostats = _by_serial(await api.get_notifications())
    thermostat = thermostats[SERIAL]
    send = AsyncMock()
    gateway.set_schedule = send  # type: ignore[method-assign]
    assert api.can_set_schedule(SERIAL)
    assert not api.can_set_schedule(OTHER)

    await api.set_schedule(thermostat, [6, 0, 6], [(time(6), 2000), (time(22), 1800)])
    send.assert_awaited_once_with(
        SERIAL, frozenset({0, 6}), [(6 * 3600, 2000), (22 * 3600, 1800)]
    )

    send.reset_mock()
    for days, events, error in (
        ([0], [(time(6), 4050)], "between 5 and 40 °C"),
        ([0], [(time(6), 450)], "between 5 and 40 °C"),
        ([0], [(time(6, 10), 2000)], "06:10: times must be on a quarter"),
        ([0], [(time(6, 0, 0, 1), 2000)], "06:00: times must be on a quarter"),
        ([0], [(time(6), 2000), (time(5), 2000)], "must be in order"),
        ([0], [], "between 1 and 6 events"),
        ([], [(time(6), 2000)], "0 for Monday"),
    ):
        with pytest.raises(OJMicrolineScheduleError, match=error):
            await api.set_schedule(thermostat, days, events)
    send.assert_not_awaited()

    # The thermostat has to be connected to the gateway.
    with pytest.raises(OJMicrolineError, match="connected to the gateway"):
        await api.set_schedule(thermostats[OTHER], [0], [(time(6), 2000)])
    send.assert_not_awaited()

    # An error of the gateway reaches the caller.
    send.side_effect = OJMicrolineConnectionError("disconnected")
    with pytest.raises(OJMicrolineConnectionError, match="disconnected"):
        await api.set_schedule(thermostat, [0], [(time(6), 2000)])
