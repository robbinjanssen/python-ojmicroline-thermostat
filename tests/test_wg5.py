# pylint: disable=protected-access
# mypy: disable-error-code="attr-defined"
"""Integration test for the WG5API class."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import aiohttp
import pytest
from aiohttp import web
from aresponses import Response, ResponsesMockServer

from ojmicroline_thermostat import (
    OJMicroline,
    OJMicrolineAuthError,
    OJMicrolineConnectionError,
    OJMicrolineError,
    Thermostat,
)
from ojmicroline_thermostat.const import REGULATION_MANUAL
from ojmicroline_thermostat.wg5 import WG5API

from . import load_fixtures


@pytest.mark.asyncio
async def test_login(aresponses: ResponsesMockServer) -> None:
    """Test the OAuth2 login method."""
    aresponses.add(
        "identity.test.host",
        "/connect/token",
        "POST",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=load_fixtures("wg5_token.json"),
        ),
    )
    async with aiohttp.ClientSession() as session:
        api = WG5API(
            username="py",
            password="test",
            host="ojmicroline.test.host",
            identity_host="identity.test.host",
        )
        client = OJMicroline(api=api, session=session)

        await client.login()
        assert api._access_token == "eyJ0ZXN0IjoiZmFrZS1hY2Nlc3MtdG9rZW4ifQ"
        assert api._refresh_token == "fake-refresh-token-for-testing"


@pytest.mark.asyncio
async def test_login_failed(aresponses: ResponsesMockServer) -> None:
    """Test the OAuth2 login method when it fails."""
    aresponses.add(
        "identity.test.host",
        "/connect/token",
        "POST",
        Response(
            status=400,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"error": "invalid_grant"}),
        ),
    )
    async with aiohttp.ClientSession() as session:
        api = WG5API(
            username="py",
            password="test",
            host="ojmicroline.test.host",
            identity_host="identity.test.host",
        )
        client = OJMicroline(api=api, session=session)

        with pytest.raises(OJMicrolineAuthError):
            await client.login()


def _add_login_response(aresponses: ResponsesMockServer) -> None:
    """Add a successful login response to the mock server."""
    aresponses.add(
        "identity.test.host",
        "/connect/token",
        "POST",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=load_fixtures("wg5_token.json"),
        ),
    )


def _make_api() -> WG5API:
    """Create a WG5API for tests."""
    return WG5API(
        username="py",
        password="test",
        host="ojmicroline.test.host",
        identity_host="identity.test.host",
    )


@pytest.mark.asyncio
async def test_login_skips_when_token_valid(aresponses: ResponsesMockServer) -> None:
    """Test that login is skipped when a valid token exists."""
    _add_login_response(aresponses)
    # Only one token response is mocked; a second login call would fail.
    aresponses.add(
        "ojmicroline.test.host",
        "/buildings",
        "GET",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"data": []}),
        ),
    )
    aresponses.add(
        "ojmicroline.test.host",
        "/buildings",
        "GET",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"data": []}),
        ),
    )
    async with aiohttp.ClientSession() as session:
        api = _make_api()
        client = OJMicroline(api=api, session=session)

        # First call triggers login
        await client.get_thermostats()
        # Second call reuses the cached token (line 79)
        await client.get_thermostats()


@pytest.mark.asyncio
async def test_get_thermostats(aresponses: ResponsesMockServer) -> None:
    """Test fetching thermostats through the buildings->tree->zone flow."""
    _add_login_response(aresponses)
    aresponses.add(
        "ojmicroline.test.host",
        "/buildings",
        "GET",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=load_fixtures("wg5_buildings.json"),
        ),
    )
    aresponses.add(
        "ojmicroline.test.host",
        "/awaymode/57dc7778-4ed3-4382-9e89-5cf2e0bc0f8a",
        "GET",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"status": {"code": "OK"}}),
        ),
    )
    aresponses.add(
        "ojmicroline.test.host",
        "/buildings/57dc7778-4ed3-4382-9e89-5cf2e0bc0f8a/tree",
        "GET",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=load_fixtures("wg5_building_tree.json"),
        ),
    )
    aresponses.add(
        "ojmicroline.test.host",
        "/zones/ae85d8fc-1b4a-445a-bdf1-05f970fc3da4/control/thermostats",
        "GET",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=load_fixtures("wg5_zone_thermostats.json"),
        ),
    )
    aresponses.add(
        "ojmicroline.test.host",
        "/thermostats/2cb3e6c5-8cf8-4e7e-943a-42618c34a506",
        "GET",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=load_fixtures("wg5_thermostat_detail.json"),
        ),
    )
    aresponses.add(
        "ojmicroline.test.host",
        "/schedules/f9aac20b-4e45-415b-9f56-e53014ee8639",
        "GET",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=load_fixtures("wg5_schedule.json"),
        ),
    )
    async with aiohttp.ClientSession() as session:
        api = _make_api()
        client = OJMicroline(api=api, session=session)

        thermostats: list[Thermostat] = await client.get_thermostats()

        assert len(thermostats) == 1
        thermostat = thermostats[0]
        assert thermostat.name == "Bathroom"
        assert thermostat.model == "WG5"
        assert thermostat.serial_number == "2cb3e6c5-8cf8-4e7e-943a-42618c34a506"
        assert thermostat.online is True
        assert thermostat.heating is True
        assert thermostat.regulation_mode == REGULATION_MANUAL
        assert thermostat.temperature == 1900
        assert thermostat.set_point_temperature == 2778
        assert thermostat.building_id == "57dc7778-4ed3-4382-9e89-5cf2e0bc0f8a"
        assert thermostat.zone_name == "Default"
        assert thermostat.software_version == "1047A108"
        assert thermostat.temperature_floor == 1900
        assert thermostat.temperature_room == 2100
        assert thermostat.schedule is not None
        assert thermostat.schedule["baseTemperature"] == 23
        assert len(thermostat.schedule["schedules"]) == 3


@pytest.mark.asyncio
async def test_get_thermostats_no_schedule_no_readouts(
    aresponses: ResponsesMockServer,
) -> None:
    """Test fetching thermostats when schedule and readouts are missing."""
    _add_login_response(aresponses)
    aresponses.add(
        "ojmicroline.test.host",
        "/buildings",
        "GET",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=load_fixtures("wg5_buildings.json"),
        ),
    )
    aresponses.add(
        "ojmicroline.test.host",
        "/awaymode/57dc7778-4ed3-4382-9e89-5cf2e0bc0f8a",
        "GET",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"status": {"code": "OK"}}),
        ),
    )
    aresponses.add(
        "ojmicroline.test.host",
        "/buildings/57dc7778-4ed3-4382-9e89-5cf2e0bc0f8a/tree",
        "GET",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=load_fixtures("wg5_building_tree.json"),
        ),
    )
    # Control data with no scheduleId
    zone_data = json.loads(load_fixtures("wg5_zone_thermostats.json"))
    zone_data["data"][0]["scheduleData"] = {}
    aresponses.add(
        "ojmicroline.test.host",
        "/zones/ae85d8fc-1b4a-445a-bdf1-05f970fc3da4/control/thermostats",
        "GET",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps(zone_data),
        ),
    )
    # Detail data with no readout temperatures
    detail_data = json.loads(load_fixtures("wg5_thermostat_detail.json"))
    del detail_data["data"]["thermostatReadouts"]["floorTemperature"]
    del detail_data["data"]["thermostatReadouts"]["roomTemperature"]
    aresponses.add(
        "ojmicroline.test.host",
        "/thermostats/2cb3e6c5-8cf8-4e7e-943a-42618c34a506",
        "GET",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps(detail_data),
        ),
    )
    async with aiohttp.ClientSession() as session:
        api = _make_api()
        client = OJMicroline(api=api, session=session)

        thermostats: list[Thermostat] = await client.get_thermostats()

        assert len(thermostats) == 1
        thermostat = thermostats[0]
        assert thermostat.schedule is None
        assert thermostat.temperature_floor is None
        assert thermostat.temperature_room is None


ZONE_ID = "ae85d8fc-1b4a-445a-bdf1-05f970fc3da4"
BUILDING_ID = "57dc7778-4ed3-4382-9e89-5cf2e0bc0f8a"
SCHEDULE_ID = "f9aac20b-4e45-415b-9f56-e53014ee8639"


def _json_response(data: object) -> Response:
    """Return a JSON response for aresponses."""
    return Response(
        status=200,
        headers={"Content-Type": "application/json"},
        text=json.dumps(data),
    )


def _add_building_responses(
    aresponses: ResponsesMockServer, tree: dict[str, object]
) -> None:
    """Add the login, buildings, away mode and tree responses."""
    _add_login_response(aresponses)
    aresponses.add(
        "ojmicroline.test.host",
        "/buildings",
        "GET",
        _json_response(json.loads(load_fixtures("wg5_buildings.json"))),
    )
    aresponses.add(
        "ojmicroline.test.host",
        f"/awaymode/{BUILDING_ID}",
        "GET",
        _json_response({"status": {"code": "OK"}}),
    )
    aresponses.add(
        "ojmicroline.test.host",
        f"/buildings/{BUILDING_ID}/tree",
        "GET",
        _json_response(tree),
    )


def _zone_thermostat(thermostat_id: str) -> dict[str, object]:
    """Return the zone control data of a thermostat with the given ID."""
    data: dict[str, object] = json.loads(load_fixtures("wg5_zone_thermostats.json"))[
        "data"
    ][0]
    data["id"] = thermostat_id
    return data


@pytest.mark.asyncio
async def test_get_thermostats_zone_pages_and_shared_schedule(
    aresponses: ResponsesMockServer,
) -> None:
    """Test fetching a zone over multiple pages, with a shared schedule."""
    tree = json.loads(load_fixtures("wg5_building_tree.json"))
    _add_building_responses(aresponses, tree)

    page_numbers: list[str] = []
    pages = [["thermostat-1", "thermostat-2"], ["thermostat-3"]]

    async def zone_handler(request: web.BaseRequest) -> Response:
        assert request.query["pageSize"] == "100"
        page_number = int(request.query["pageNumber"])
        page_numbers.append(request.query["pageNumber"])
        return _json_response(
            {
                "page": {"total": 3, "totalPages": 2, "size": 100},
                "data": [_zone_thermostat(tid) for tid in pages[page_number]],
                "status": {"code": "OK"},
            }
        )

    for _ in pages:
        aresponses.add(
            "ojmicroline.test.host",
            f"/zones/{ZONE_ID}/control/thermostats",
            "GET",
            zone_handler,
        )
    detail = json.loads(load_fixtures("wg5_thermostat_detail.json"))
    for thermostat_id in ("thermostat-1", "thermostat-2", "thermostat-3"):
        aresponses.add(
            "ojmicroline.test.host",
            f"/thermostats/{thermostat_id}",
            "GET",
            _json_response(detail),
        )
    # The thermostats share one schedule, which is only fetched once.
    aresponses.add(
        "ojmicroline.test.host",
        f"/schedules/{SCHEDULE_ID}",
        "GET",
        _json_response(json.loads(load_fixtures("wg5_schedule.json"))),
    )

    async with aiohttp.ClientSession() as session:
        client = OJMicroline(api=_make_api(), session=session)
        thermostats = await client.get_thermostats()

    # Every route was used exactly once and no other requests were made.
    aresponses.assert_no_unused_routes()
    aresponses.assert_all_requests_matched()
    assert page_numbers == ["0", "1"]
    assert [t.serial_number for t in thermostats] == [
        "thermostat-1",
        "thermostat-2",
        "thermostat-3",
    ]
    assert all(t.schedule is not None for t in thermostats)
    # Every thermostat has its own copy of the shared schedule.
    assert thermostats[0].schedule is not thermostats[1].schedule


@pytest.mark.asyncio
async def test_get_thermostats_skips_empty_zone(
    aresponses: ResponsesMockServer,
) -> None:
    """Test that zones without thermostats are not requested."""
    tree = json.loads(load_fixtures("wg5_building_tree.json"))
    tree["data"]["zones"][0]["thermostats"] = []
    _add_building_responses(aresponses, tree)

    async with aiohttp.ClientSession() as session:
        client = OJMicroline(api=_make_api(), session=session)
        assert await client.get_thermostats() == []

    aresponses.assert_plan_strictly_followed()


@pytest.mark.asyncio
async def test_get_energy_usage_no_building_id() -> None:
    """Test energy usage returns empty list when building_id is missing."""
    data = json.loads(load_fixtures("wg5_thermostat_control.json"))
    thermostat = Thermostat.from_wg5_json(
        data["data"],
        building_id="",
        zone_name="Default",
    )

    async with aiohttp.ClientSession() as session:
        api = _make_api()
        client = OJMicroline(api=api, session=session)
        # No login mock needed; should return before making any request
        api._access_token = "fake"
        api._token_expiry = datetime.now(tz=UTC) + timedelta(hours=1)

        energy = await client.get_energy_usage(thermostat)
        assert energy == []


@pytest.mark.asyncio
async def test_get_energy_usage(aresponses: ResponsesMockServer) -> None:
    """Test fetching energy usage for a WG5 thermostat."""
    _add_login_response(aresponses)
    aresponses.add(
        "ojmicroline.test.host",
        "/energy/usage/building/57dc7778-4ed3-4382-9e89-5cf2e0bc0f8a",
        "POST",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=load_fixtures("wg5_energy.json"),
        ),
    )
    async with aiohttp.ClientSession() as session:
        api = _make_api()
        client = OJMicroline(api=api, session=session)

        data = json.loads(load_fixtures("wg5_thermostat_control.json"))
        thermostat = Thermostat.from_wg5_json(
            data["data"],
            building_id="57dc7778-4ed3-4382-9e89-5cf2e0bc0f8a",
            zone_name="Default",
        )

        energy = await client.get_energy_usage(thermostat)
        assert energy == [150.0, 200.0, 0.0]


@pytest.mark.asyncio
async def test_notifications_unsupported() -> None:
    """Test that push notifications are reported as unsupported."""
    api = WG5API(
        host="ojmicroline.test.host",
        username="py",
        password="test",
    )
    assert api.supports_notifications is False
    with pytest.raises(OJMicrolineError):
        await api.get_notifications()


def test_invalidate_session() -> None:
    """Test invalidating the session forgets the access token."""
    api = _make_api()
    api._access_token = "fake"
    api._token_expiry = datetime.now(tz=UTC) + timedelta(hours=1)

    api.invalidate_session()

    assert api._access_token is None
    assert api._token_expiry is None


@pytest.mark.asyncio
async def test_login_uses_shared_session(aresponses: ResponsesMockServer) -> None:
    """Test logging in posts a form through the session passed to OJMicroline."""

    async def token_handler(request: web.Request) -> Response:
        assert request.content_type == "application/x-www-form-urlencoded"
        form = await request.post()
        assert form["grant_type"] == "password"
        assert form["username"] == "py"
        assert form["client_id"] == "mobile_app_client"
        return Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=load_fixtures("wg5_token.json"),
        )

    aresponses.add("identity.test.host", "/connect/token", "POST", token_handler)
    async with aiohttp.ClientSession() as session:
        api = _make_api()
        client = OJMicroline(api=api, session=session)

        with patch.object(session, "request", wraps=session.request) as request:
            await client.login()

        assert request.call_count == 1
        assert api._access_token is not None


@pytest.mark.asyncio
async def test_login_server_error(aresponses: ResponsesMockServer) -> None:
    """Test a server error is a connection error, not wrong credentials."""
    aresponses.add("identity.test.host", "/connect/token", "POST", Response(status=500))
    async with aiohttp.ClientSession() as session:
        client = OJMicroline(api=_make_api(), session=session)

        with pytest.raises(OJMicrolineConnectionError):
            await client.login()


def _expired_api(refresh_token: str | None = "r3fr3sh") -> WG5API:
    """Create a WG5API with an access token that is about to expire."""
    api = _make_api()
    api._access_token = "old"
    api._refresh_token = refresh_token
    # Within the 30 second renewal margin, so login() renews it.
    api._token_expiry = datetime.now(tz=UTC) + timedelta(seconds=10)
    return api


@pytest.mark.asyncio
async def test_login_uses_refresh_token(aresponses: ResponsesMockServer) -> None:
    """Test an expiring access token is renewed with the refresh token."""
    grants: list[dict[str, str]] = []

    async def token_handler(request: web.Request) -> Response:
        form = await request.post()
        grants.append({key: str(value) for key, value in form.items()})
        return Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=load_fixtures("wg5_token.json"),
        )

    aresponses.add("identity.test.host", "/connect/token", "POST", token_handler)
    async with aiohttp.ClientSession() as session:
        api = _expired_api()
        await OJMicroline(api=api, session=session).login()

    assert len(grants) == 1
    assert grants[0]["grant_type"] == "refresh_token"
    assert grants[0]["refresh_token"] == "r3fr3sh"
    assert "password" not in grants[0]
    assert api._access_token != "old"


@pytest.mark.asyncio
async def test_login_falls_back_to_password(aresponses: ResponsesMockServer) -> None:
    """Test a rejected refresh token falls back to the password grant."""
    grants: list[str] = []

    async def token_handler(request: web.Request) -> Response:
        grant = (await request.post())["grant_type"]
        grants.append(str(grant))
        if grant == "refresh_token":
            return Response(status=400, text='{"error": "invalid_grant"}')
        return Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=load_fixtures("wg5_token.json"),
        )

    aresponses.add(
        "identity.test.host", "/connect/token", "POST", token_handler, repeat=2
    )
    async with aiohttp.ClientSession() as session:
        api = _expired_api()
        await OJMicroline(api=api, session=session).login()

    assert grants == ["refresh_token", "password"]
    assert api._access_token != "old"


@pytest.mark.asyncio
async def test_login_without_refresh_token_uses_password(
    aresponses: ResponsesMockServer,
) -> None:
    """Test the password grant is used when there is no refresh token."""
    grants: list[str] = []

    async def token_handler(request: web.Request) -> Response:
        grants.append(str((await request.post())["grant_type"]))
        return Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=load_fixtures("wg5_token.json"),
        )

    aresponses.add("identity.test.host", "/connect/token", "POST", token_handler)
    async with aiohttp.ClientSession() as session:
        await OJMicroline(api=_expired_api(None), session=session).login()

    assert grants == ["password"]
