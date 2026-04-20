# pylint: disable=protected-access
# mypy: disable-error-code="attr-defined"
"""Integration test for the WG5API class."""

import json
from datetime import UTC, datetime, timedelta

import aiohttp
import pytest
from aresponses import Response, ResponsesMockServer  # type: ignore[import]
from ojmicroline_thermostat import (
    OJMicroline,
    OJMicrolineAuthError,
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
    """Test fetching thermostats through the buildings->tree->control flow."""
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
        "/thermostats/2cb3e6c5-8cf8-4e7e-943a-42618c34a506/control",
        "GET",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=load_fixtures("wg5_thermostat_control.json"),
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
    control_data = json.loads(load_fixtures("wg5_thermostat_control.json"))
    control_data["data"]["scheduleData"] = {}
    aresponses.add(
        "ojmicroline.test.host",
        "/thermostats/2cb3e6c5-8cf8-4e7e-943a-42618c34a506/control",
        "GET",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps(control_data),
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
