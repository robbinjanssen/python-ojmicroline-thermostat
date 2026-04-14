# pylint: disable=protected-access
# mypy: disable-error-code=attr-defined
"""Integration test for the WD5API class."""

import json
from unittest.mock import patch

import aiohttp
import pytest
from aresponses import Response, ResponsesMockServer  # type: ignore[import]
from ojmicroline_thermostat import (
    WD5API,
    OJMicroline,
    OJMicrolineAuthError,
    OJMicrolineError,
    OJMicrolineResultsError,
    Thermostat,
)
from ojmicroline_thermostat.const import REGULATION_COMFORT, REGULATION_MANUAL

from . import load_fixtures


@pytest.mark.asyncio
async def test_login(aresponses: ResponsesMockServer) -> None:
    """Test the login method."""
    aresponses.add(
        "ojmicroline.test.host",
        "/api/UserProfile/SignIn",
        "POST",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"SessionId": "f00br4", "UserName": "py", "ErrorCode": 0}),
        ),
    )
    async with aiohttp.ClientSession() as session:
        api = WD5API(
            host="ojmicroline.test.host",
            api_key="ap1-k3y-v3ry-s3cret",
            customer_id=1337,
            username="py",
            password="test",
        )
        client = OJMicroline(api=api, session=session)

        await client.login()
        assert api._session_calls_left == api._session_calls
        assert api._session_id == "f00br4"


@pytest.mark.asyncio
async def test_login_failed(aresponses: ResponsesMockServer) -> None:
    """Test the login method when it fails."""
    aresponses.add(
        "ojmicroline.test.host",
        "/api/UserProfile/SignIn",
        "POST",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"ErrorCode": 1}),
        ),
    )
    async with aiohttp.ClientSession() as session:
        api = WD5API(
            host="ojmicroline.test.host",
            api_key="ap1-k3y-v3ry-s3cret",
            customer_id=1337,
            username="py",
            password="test",
        )
        client = OJMicroline(api=api, session=session)

        with pytest.raises(OJMicrolineAuthError):
            await client.login()

        assert api._session_calls_left == -1
        assert api._session_id is None


@pytest.mark.asyncio
async def test_get_thermostats(aresponses: ResponsesMockServer) -> None:
    """Test get thermostats function and make sure fields are set."""
    aresponses.add(
        "ojmicroline.test.host",
        "/api/Group/GroupContents",
        "GET",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=load_fixtures("wd5_group.json"),
        ),
    )
    aresponses.add(
        "ojmicroline.test.host",
        "/api/EnergyUsage/GetEnergyUsage",
        "POST",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=load_fixtures("energy.json"),
        ),
    )
    aresponses.add(
        "ojmicroline.test.host",
        "/api/EnergyUsage/GetEnergyUsage",
        "POST",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=load_fixtures("energy.json"),
        ),
    )

    async with aiohttp.ClientSession() as session:
        api = WD5API(
            host="ojmicroline.test.host",
            api_key="ap1-k3y-v3ry-s3cret",
            customer_id=1337,
            username="py",
            password="test",
        )
        api._session_calls_left = 300
        api._session_id = "f00b4r"
        client = OJMicroline(api=api, session=session)

        thermostats: list[Thermostat] = await client.get_thermostats()

        assert thermostats is not None
        for item in thermostats:
            assert item.serial_number is not None


@pytest.mark.asyncio
async def test_get_thermostats_failed(aresponses: ResponsesMockServer) -> None:
    """Test get thermostats function to handle an error."""
    aresponses.add(
        "ojmicroline.test.host",
        "/api/Group/GroupContents",
        "GET",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"ErrorCode": 1}),
        ),
    )
    async with aiohttp.ClientSession() as session:
        api = WD5API(
            host="ojmicroline.test.host",
            api_key="ap1-k3y-v3ry-s3cret",
            customer_id=1337,
            username="py",
            password="test",
        )
        api._session_calls_left = 300
        api._session_id = "f00b4r"
        client = OJMicroline(api=api, session=session)

        with pytest.raises(OJMicrolineResultsError):
            await client.get_thermostats()


@pytest.mark.asyncio
async def test_set_regulation_mode(aresponses: ResponsesMockServer) -> None:
    """Test updating the regulation mode."""
    aresponses.add(
        "ojmicroline.test.host",
        "/api/Group/UpdateGroup",
        "POST",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"ErrorCode": 0}),
        ),
    )
    async with aiohttp.ClientSession() as session:
        data = load_fixtures("wd5_thermostat.json")
        thermostat = Thermostat.from_wd5_json(json.loads(data))

        api = WD5API(
            host="ojmicroline.test.host",
            api_key="ap1-k3y-v3ry-s3cret",
            customer_id=1337,
            username="py",
            password="test",
        )
        api._session_calls_left = 300
        api._session_id = "f00b4r"
        client = OJMicroline(api=api, session=session)

        result = await client.set_regulation_mode(thermostat, REGULATION_MANUAL, 2500)

        assert result is True


@pytest.mark.asyncio
async def test_set_regulation_mode_expect_login(
    aresponses: ResponsesMockServer,
) -> None:
    """Test update the regulation mode with login method fired."""
    aresponses.add(
        "ojmicroline.test.host",
        "/api/Group/UpdateGroup",
        "POST",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"ErrorCode": 0}),
        ),
    )
    async with aiohttp.ClientSession() as session:
        api = WD5API(
            host="ojmicroline.test.host",
            api_key="ap1-k3y-v3ry-s3cret",
            customer_id=1337,
            username="py",
            password="test",
        )

        data = load_fixtures("wd5_thermostat.json")
        thermostat = Thermostat.from_wd5_json(json.loads(data))

        def set_session_id() -> None:
            api._session_id = "f00b4r"

        with patch.object(
            OJMicroline, "login", side_effect=set_session_id
        ) as mock_login:
            client = OJMicroline(api=api, session=session)
            await client.set_regulation_mode(thermostat, REGULATION_MANUAL, None)

        mock_login.assert_called_once()


@pytest.mark.asyncio
async def test_set_regulation_mode_failed(aresponses: ResponsesMockServer) -> None:
    """Test update the regulation mode when an error occurs."""
    aresponses.add(
        "ojmicroline.test.host",
        "/api/Group/UpdateGroup",
        "POST",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"ErrorCode": 1}),
        ),
    )
    async with aiohttp.ClientSession() as session:
        data = load_fixtures("wd5_thermostat.json")
        thermostat = Thermostat.from_wd5_json(json.loads(data))

        api = WD5API(
            host="ojmicroline.test.host",
            api_key="ap1-k3y-v3ry-s3cret",
            customer_id=1337,
            username="py",
            password="test",
        )
        api._session_calls_left = 300
        api._session_id = "f00b4r"
        client = OJMicroline(api=api, session=session)

        with pytest.raises(OJMicrolineError):
            await client.set_regulation_mode(thermostat, REGULATION_COMFORT, 2500, 360)
