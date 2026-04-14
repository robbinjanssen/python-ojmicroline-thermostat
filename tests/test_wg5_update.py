# pylint: disable=protected-access
# mypy: disable-error-code="attr-defined"
"""Test the WG5API update regulation mode methods."""

import json

import aiohttp
import pytest
from aresponses import Response, ResponsesMockServer  # type: ignore[import]
from freezegun import freeze_time
from ojmicroline_thermostat import (
    OJMicroline,
    Thermostat,
)
from ojmicroline_thermostat.const import (
    REGULATION_COMFORT,
    REGULATION_FROST_PROTECTION,
    REGULATION_MANUAL,
    REGULATION_SCHEDULE,
    REGULATION_VACATION,
)
from ojmicroline_thermostat.wg5 import WG5API

from . import load_fixtures


def _make_api() -> WG5API:
    """Create a WG5API for tests."""
    return WG5API(
        username="py",
        password="test",
        host="ojmicroline.test.host",
        identity_host="identity.test.host",
    )


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


def _make_thermostat() -> Thermostat:
    """Create a WG5 thermostat for tests."""
    data = json.loads(load_fixtures("wg5_thermostat_control.json"))
    return Thermostat.from_wg5_json(
        data["data"],
        building_id="57dc7778-4ed3-4382-9e89-5cf2e0bc0f8a",
        zone_name="Default",
    )


@pytest.mark.asyncio
async def test_set_regulation_mode_manual(aresponses: ResponsesMockServer) -> None:
    """Test setting manual mode with a temperature."""
    _add_login_response(aresponses)
    aresponses.add(
        "ojmicroline.test.host",
        "/thermostats/2cb3e6c5-8cf8-4e7e-943a-42618c34a506/mode",
        "PUT",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"data": {"changeTimestamp": "2026-04-13T23:40:43Z"}}),
        ),
    )
    async with aiohttp.ClientSession() as session:
        api = _make_api()
        client = OJMicroline(api=api, session=session)
        thermostat = _make_thermostat()

        result = await client.set_regulation_mode(thermostat, REGULATION_MANUAL, 2600)
        assert result is True


@pytest.mark.asyncio
@freeze_time("2026-04-13 23:40:00")
async def test_set_regulation_mode_comfort(aresponses: ResponsesMockServer) -> None:
    """Test setting comfort mode with duration."""
    _add_login_response(aresponses)
    aresponses.add(
        "ojmicroline.test.host",
        "/thermostats/2cb3e6c5-8cf8-4e7e-943a-42618c34a506/mode",
        "PUT",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"data": {"changeTimestamp": "2026-04-13T23:40:43Z"}}),
        ),
    )
    async with aiohttp.ClientSession() as session:
        api = _make_api()
        client = OJMicroline(api=api, session=session)
        thermostat = _make_thermostat()

        result = await client.set_regulation_mode(
            thermostat, REGULATION_COMFORT, 2200, 120
        )
        assert result is True


@pytest.mark.asyncio
async def test_set_regulation_mode_schedule(aresponses: ResponsesMockServer) -> None:
    """Test setting schedule mode."""
    _add_login_response(aresponses)
    aresponses.add(
        "ojmicroline.test.host",
        "/thermostats/2cb3e6c5-8cf8-4e7e-943a-42618c34a506/mode",
        "PUT",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"data": {"changeTimestamp": "2026-04-13T23:40:43Z"}}),
        ),
    )
    async with aiohttp.ClientSession() as session:
        api = _make_api()
        client = OJMicroline(api=api, session=session)
        thermostat = _make_thermostat()

        result = await client.set_regulation_mode(thermostat, REGULATION_SCHEDULE)
        assert result is True


@pytest.mark.asyncio
async def test_set_regulation_mode_frost_protection(
    aresponses: ResponsesMockServer,
) -> None:
    """Test setting frost protection (standby) mode."""
    _add_login_response(aresponses)
    aresponses.add(
        "ojmicroline.test.host",
        "/thermostats/2cb3e6c5-8cf8-4e7e-943a-42618c34a506/standby/True",
        "PUT",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"data": {"changeTimestamp": "2026-04-13T23:41:50Z"}}),
        ),
    )
    async with aiohttp.ClientSession() as session:
        api = _make_api()
        client = OJMicroline(api=api, session=session)
        thermostat = _make_thermostat()

        result = await client.set_regulation_mode(
            thermostat, REGULATION_FROST_PROTECTION
        )
        assert result is True


@pytest.mark.asyncio
async def test_set_regulation_mode_vacation(
    aresponses: ResponsesMockServer,
) -> None:
    """Test enabling away (vacation) mode."""
    _add_login_response(aresponses)
    aresponses.add(
        "ojmicroline.test.host",
        "/awaymode",
        "POST",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"status": {"code": "OK"}}),
        ),
    )
    async with aiohttp.ClientSession() as session:
        api = _make_api()
        client = OJMicroline(api=api, session=session)
        thermostat = _make_thermostat()

        result = await client.set_regulation_mode(thermostat, REGULATION_VACATION)
        assert result is True


@pytest.mark.asyncio
async def test_set_regulation_mode_disable_vacation(
    aresponses: ResponsesMockServer,
) -> None:
    """Test switching from vacation to schedule disables away mode."""
    _add_login_response(aresponses)
    aresponses.add(
        "ojmicroline.test.host",
        "/awaymode/57dc7778-4ed3-4382-9e89-5cf2e0bc0f8a",
        "DELETE",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"status": {"code": "OK"}}),
        ),
    )
    aresponses.add(
        "ojmicroline.test.host",
        "/thermostats/2cb3e6c5-8cf8-4e7e-943a-42618c34a506/mode",
        "PUT",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"data": {"changeTimestamp": "2026-04-14T01:00:00Z"}}),
        ),
    )
    async with aiohttp.ClientSession() as session:
        api = _make_api()
        client = OJMicroline(api=api, session=session)
        thermostat = _make_thermostat()
        thermostat.vacation_mode = True

        result = await client.set_regulation_mode(thermostat, REGULATION_SCHEDULE)
        assert result is True
