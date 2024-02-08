# pylint: disable=protected-access
# mypy: disable-error-code=attr-defined
"""Test the main OJMicroline class internals."""
import asyncio
import json
from unittest.mock import patch

import aiohttp
import pytest
from aresponses import Response, ResponsesMockServer  # type: ignore[import]
from ojmicroline_thermostat import (
    WG4API,
    OJMicroline,
    OJMicrolineConnectionError,
    OJMicrolineError,
    OJMicrolineTimeoutError,
)

from . import load_fixtures


@pytest.mark.asyncio
async def test_json_request(aresponses: ResponsesMockServer) -> None:
    """Test JSON response is handled correctly."""
    aresponses.add(
        "ojmicroline.test.host",
        "/test",
        "GET",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=load_fixtures("wg4_group.json"),
        ),
    )
    async with aiohttp.ClientSession() as session:
        client = OJMicroline(
            api=WG4API(
                host="ojmicroline.test.host",
                username="py",
                password="test",
            ),
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
        return Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"ErrorCode": 0}),
        )

    aresponses.add("ojmicroline.test.host", "/test", "GET", response_handler)

    async with aiohttp.ClientSession() as session:
        client = OJMicroline(
            api=WG4API(
                host="ojmicroline.test.host",
                username="py",
                password="test",
            ),
            session=session,
        )
        monkeypatch.setattr(client, "_OJMicroline__request_timeout", 0.1)

        with pytest.raises(OJMicrolineTimeoutError):
            assert await client._request("test")


@pytest.mark.asyncio
async def test_content_type(aresponses: ResponsesMockServer) -> None:
    """Test request content type error."""
    aresponses.add(
        "ojmicroline.test.host",
        "/test",
        "GET",
        Response(
            status=200,
            headers={"Content-Type": "foo/Bar"},
        ),
    )

    async with aiohttp.ClientSession() as session:
        client = OJMicroline(
            api=WG4API(
                host="ojmicroline.test.host",
                username="py",
                password="test",
            ),
            session=session,
        )

        with pytest.raises(OJMicrolineError):
            assert await client._request("test")


@pytest.mark.asyncio
async def test_client_error() -> None:
    """Test request connection exception from the API."""
    async with aiohttp.ClientSession() as session:
        client = OJMicroline(
            api=WG4API(
                host="ojmicroline.test.host",
                username="py",
                password="test",
            ),
            session=session,
        )

        with (
            patch.object(session, "request", side_effect=aiohttp.ClientError),
            pytest.raises(OJMicrolineConnectionError),
        ):
            assert await client._request("test")


@pytest.mark.asyncio
async def test_internal_session(aresponses: ResponsesMockServer) -> None:
    """Test internal session is handled correctly."""
    aresponses.add(
        "ojmicroline.test.host",
        "/test",
        "GET",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"ErrorCode": 0}),
        ),
    )
    async with OJMicroline(
        api=WG4API(
            host="ojmicroline.test.host",
            username="py",
            password="test",
        ),
    ) as client:
        await client._request("/test")
