# pylint: disable=protected-access
# mypy: disable-error-code=attr-defined
"""Test the models."""
import asyncio
import json
from unittest.mock import patch

import aiohttp
import pytest
from aresponses import Response, ResponsesMockServer  # type: ignore[import]

from ojmicroline_thermostat import (
    OJMicroline,
    OJMicrolineAuthException,
    OJMicrolineConnectionException,
    OJMicrolineException,
    OJMicrolineResultsException,
    OJMicrolineTimeoutException,
    Thermostat,
)
from ojmicroline_thermostat.const import REGULATION_MANUAL

from . import load_fixtures


@pytest.mark.asyncio
async def test_json_request(aresponses: ResponsesMockServer) -> None:
    """Test JSON response is handled correctly."""
    aresponses.add(
        "owd5.test.py",
        "/test",
        "GET",
        aresponses.Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=load_fixtures("group_contents.json"),
        ),
    )
    async with aiohttp.ClientSession() as session:
        client = OJMicroline(
            host="owd5.test.py",
            api_key="ap1-k3y-v3ry-s3cret",
            customer_id=1337,
            username="py",
            password="test",
            session=session,
        )
        response = await client._request("test")
        assert response is not None
        await client.close()
        assert client._OJMicroline__close_http_session is False
        assert client._OJMicroline__session_id is None


@pytest.mark.asyncio
async def test_timeout(monkeypatch, aresponses: ResponsesMockServer) -> None:
    """Test request timeout."""

    async def response_handler(_: aiohttp.ClientResponse) -> Response:
        # Faking a timeout by sleeping
        await asyncio.sleep(0.2)
        return aresponses.Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"ErrorCode": 0}),
        )

    aresponses.add("owd5.test.py", "/test", "GET", response_handler)

    async with aiohttp.ClientSession() as session:
        client = OJMicroline(
            host="owd5.test.py",
            api_key="ap1-k3y-v3ry-s3cret",
            customer_id=1337,
            username="py",
            password="test",
            session=session,
        )
        monkeypatch.setattr(client, "_OJMicroline__request_timeout", 0.1)

        with pytest.raises(OJMicrolineTimeoutException):
            assert await client._request("test")


@pytest.mark.asyncio
async def test_content_type(aresponses: ResponsesMockServer) -> None:
    """Test request content type error."""
    aresponses.add(
        "owd5.test.py",
        "/test",
        "GET",
        aresponses.Response(
            status=200,
            headers={"Content-Type": "foo/Bar"},
        ),
    )

    async with aiohttp.ClientSession() as session:
        client = OJMicroline(
            host="owd5.test.py",
            api_key="ap1-k3y-v3ry-s3cret",
            customer_id=1337,
            username="py",
            password="test",
            session=session,
        )

        with pytest.raises(OJMicrolineException):
            assert await client._request("test")


@pytest.mark.asyncio
async def test_client_error() -> None:
    """Test request connection exception from the API."""
    async with aiohttp.ClientSession() as session:
        client = OJMicroline(
            host="owd5.test.py",
            api_key="ap1-k3y-v3ry-s3cret",
            customer_id=1337,
            username="py",
            password="test",
            session=session,
        )

        with patch.object(
            session, "request", side_effect=aiohttp.ClientError
        ), pytest.raises(OJMicrolineConnectionException):
            assert await client._request("test")


@pytest.mark.asyncio
async def test_internal_session(aresponses: ResponsesMockServer) -> None:
    """Test internal session is handled correctly."""
    aresponses.add(
        "owd5.test.py",
        "/test",
        "GET",
        aresponses.Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"ErrorCode": 0}),
        ),
    )
    async with OJMicroline(
        host="owd5.test.py",
        api_key="ap1-k3y-v3ry-s3cret",
        customer_id=1337,
        username="py",
        password="test",
    ) as client:
        await client._request("/test")


@pytest.mark.asyncio
async def test_login(aresponses: ResponsesMockServer) -> None:
    """Test the login method."""
    aresponses.add(
        "owd5.test.py",
        "/api/UserProfile/SignIn",
        "POST",
        aresponses.Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"SessionId": "f00br4", "UserName": "py", "ErrorCode": 0}),
        ),
    )
    async with aiohttp.ClientSession() as session:
        client = OJMicroline(
            host="owd5.test.py",
            api_key="ap1-k3y-v3ry-s3cret",
            customer_id=1337,
            username="py",
            password="test",
            session=session,
        )

        await client.login()
        assert (
            client._OJMicroline__session_calls_left
            == client._OJMicroline__session_calls
        )  # noqa: E501
        assert client._OJMicroline__session_id == "f00br4"


@pytest.mark.asyncio
async def test_login_failed(aresponses: ResponsesMockServer) -> None:
    """Test the login method when it fails."""
    aresponses.add(
        "owd5.test.py",
        "/api/UserProfile/SignIn",
        "POST",
        aresponses.Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"ErrorCode": 1}),
        ),
    )
    async with aiohttp.ClientSession() as session:
        client = OJMicroline(
            host="owd5.test.py",
            api_key="ap1-k3y-v3ry-s3cret",
            customer_id=1337,
            username="py",
            password="test",
            session=session,
        )

        with pytest.raises(OJMicrolineAuthException):
            await client.login()

        assert client._OJMicroline__session_calls_left == -1
        assert client._OJMicroline__session_id is None


@pytest.mark.asyncio
async def test_get_thermostats(monkeypatch, aresponses: ResponsesMockServer) -> None:
    """Test get thermostats function and make sure fields are set."""
    aresponses.add(
        "owd5.test.py",
        "/api/Group/GroupContents",
        "GET",
        aresponses.Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=load_fixtures("group_contents.json"),
        ),
    )
    async with aiohttp.ClientSession() as session:
        client = OJMicroline(
            host="owd5.test.py",
            api_key="ap1-k3y-v3ry-s3cret",
            customer_id=1337,
            username="py",
            password="test",
            session=session,
        )

        monkeypatch.setattr(client, "_OJMicroline__session_calls_left", 300)
        monkeypatch.setattr(client, "_OJMicroline__session_id", "f00b4r")
        thermostats: list[Thermostat] = await client.get_thermostats()

        assert thermostats is not None
        for item in thermostats:
            assert item.serial_number is not None


@pytest.mark.asyncio
async def test_get_thermostats_failed(
    monkeypatch, aresponses: ResponsesMockServer
) -> None:
    """Test get thermostats function to handle an error."""
    aresponses.add(
        "owd5.test.py",
        "/api/Group/GroupContents",
        "GET",
        aresponses.Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"ErrorCode": 1}),
        ),
    )
    async with aiohttp.ClientSession() as session:
        client = OJMicroline(
            host="owd5.test.py",
            api_key="ap1-k3y-v3ry-s3cret",
            customer_id=1337,
            username="py",
            password="test",
            session=session,
        )

        monkeypatch.setattr(client, "_OJMicroline__session_calls_left", 300)
        monkeypatch.setattr(client, "_OJMicroline__session_id", "f00b4r")

        with pytest.raises(OJMicrolineResultsException):
            await client.get_thermostats()

        assert client._OJMicroline__session_calls_left == 299


@pytest.mark.asyncio
async def test_set_regulation_mode(
    monkeypatch, aresponses: ResponsesMockServer
) -> None:
    """Test updating the regulation mode."""
    aresponses.add(
        "owd5.test.py",
        "/api/Group/UpdateGroup",
        "POST",
        aresponses.Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"ErrorCode": 0}),
        ),
    )
    async with aiohttp.ClientSession() as session:
        data = load_fixtures("single_thermostat.json")
        thermostat = Thermostat.from_json(json.loads(data))

        client = OJMicroline(
            host="owd5.test.py",
            api_key="ap1-k3y-v3ry-s3cret",
            customer_id=1337,
            username="py",
            password="test",
            session=session,
        )

        monkeypatch.setattr(client, "_OJMicroline__session_calls_left", 300)
        monkeypatch.setattr(client, "_OJMicroline__session_id", "f00b4r")
        result = await client.set_regulation_mode(thermostat, REGULATION_MANUAL, 2500)

        assert result is True


@pytest.mark.asyncio
async def test_set_regulation_mode_expect_login(
    monkeypatch, aresponses: ResponsesMockServer
) -> None:
    """Test update the regulation mode with login method fired."""
    aresponses.add(
        "owd5.test.py",
        "/api/Group/UpdateGroup",
        "POST",
        aresponses.Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"ErrorCode": 0}),
        ),
    )
    async with aiohttp.ClientSession() as session:

        def set_session_id() -> None:
            monkeypatch.setattr(client, "_OJMicroline__session_id", "f00b4r")

        data = load_fixtures("single_thermostat.json")
        thermostat = Thermostat.from_json(json.loads(data))

        with patch.object(
            OJMicroline, "login", side_effect=set_session_id
        ) as mock_login:
            client = OJMicroline(
                host="owd5.test.py",
                api_key="ap1-k3y-v3ry-s3cret",
                customer_id=1337,
                username="py",
                password="test",
                session=session,
            )
            await client.set_regulation_mode(thermostat, REGULATION_MANUAL, None)

        mock_login.assert_called_once()


@pytest.mark.asyncio
async def test_set_regulation_mode_failed(
    monkeypatch, aresponses: ResponsesMockServer
) -> None:
    """Test update the regulation mode when an error occurs."""

    aresponses.add(
        "owd5.test.py",
        "/api/Group/UpdateGroup",
        "POST",
        aresponses.Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"ErrorCode": 1}),
        ),
    )
    async with aiohttp.ClientSession() as session:
        data = load_fixtures("single_thermostat.json")
        thermostat = Thermostat.from_json(json.loads(data))

        client = OJMicroline(
            host="owd5.test.py",
            api_key="ap1-k3y-v3ry-s3cret",
            customer_id=1337,
            username="py",
            password="test",
            session=session,
        )

        monkeypatch.setattr(client, "_OJMicroline__session_calls_left", 300)
        monkeypatch.setattr(client, "_OJMicroline__session_id", "f00b4r")

        with pytest.raises(OJMicrolineException):
            await client.set_regulation_mode(thermostat, REGULATION_MANUAL, 2500)

        assert client._OJMicroline__session_calls_left == 299
