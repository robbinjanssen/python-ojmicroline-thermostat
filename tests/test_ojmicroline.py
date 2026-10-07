# pylint: disable=protected-access,unused-argument,missing-function-docstring
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
    OJMicrolineUnauthorizedError,
    Thermostat,
)
from ojmicroline_thermostat.ojmicroline import RequestFunc

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


@pytest.mark.asyncio
async def test_timeout(monkeypatch, aresponses: ResponsesMockServer) -> None:
    """Test request timeout."""

    async def response_handler(_: aiohttp.ClientResponse) -> None:
        # Faking a timeout by sleeping
        await asyncio.sleep(0.2)

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


class FakeNotificationAPI:
    """API stub that replays scripted results from get_notifications()."""

    host = "ojmicroline.test.host"
    request: RequestFunc
    supports_notifications = True

    def __init__(self, results: list[list[Thermostat] | Exception]) -> None:
        """Store the scripted results; the stub blocks once they run out."""
        self.results = results
        self.logins = 0
        self.invalidations = 0

    async def login(self) -> None:  # noqa: D102
        self.logins += 1

    def invalidate_session(self) -> None:  # noqa: D102
        self.invalidations += 1

    async def get_thermostats(self) -> list[Thermostat]:  # noqa: D102
        return []

    async def get_energy_usage(self, resource: Thermostat) -> list[float]:  # noqa: ARG002, D102
        return []

    async def set_regulation_mode(  # noqa: D102
        self,
        resource: Thermostat,  # noqa: ARG002
        regulation_mode: int,  # noqa: ARG002
        temperature: int | None,  # noqa: ARG002
        duration: int,  # noqa: ARG002
    ) -> bool:
        return True

    async def get_notifications(self) -> list[Thermostat]:  # noqa: D102
        if not self.results:
            await asyncio.Event().wait()
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _thermostat(name: str) -> Thermostat:
    thermostat = Thermostat.from_wg4_json(
        json.loads(load_fixtures("wg4_thermostat.json"))
    )
    thermostat.name = name
    return thermostat


@pytest.mark.asyncio
async def test_subscribe_dispatches_notifications() -> None:
    """Test that listeners receive every notified thermostat."""
    api = FakeNotificationAPI([[_thermostat("a")], [], [_thermostat("b")]])
    received: list[str] = []
    received_async: list[str] = []
    done = asyncio.Event()

    def listener(thermostat: Thermostat) -> None:
        received.append(thermostat.name)

    async def async_listener(thermostat: Thermostat) -> None:
        received_async.append(thermostat.name)
        if len(received_async) == 2:
            done.set()

    async with OJMicroline(api=api) as client:
        unsubscribe = client.subscribe(listener)
        unsubscribe_async = client.subscribe(async_listener)
        task = client._OJMicroline__notification_task
        assert task is not None

        await asyncio.wait_for(done.wait(), 5)
        assert received == ["a", "b"]
        assert received_async == ["a", "b"]
        assert api.logins >= 3

        unsubscribe()
        assert client._OJMicroline__notification_task is task
        unsubscribe_async()
        unsubscribe_async()
        assert client._OJMicroline__notification_task is None
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_subscribe_retries_after_error(caplog) -> None:
    """Test that a failed wait is logged and retried with a delay."""
    api = FakeNotificationAPI(
        [OJMicrolineConnectionError("boom"), KeyError("Thermostat"), [_thermostat("a")]]
    )
    done = asyncio.Event()
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    with patch("ojmicroline_thermostat.ojmicroline.asyncio.sleep", fake_sleep):
        async with OJMicroline(api=api) as client:
            client.subscribe(lambda _thermostat: done.set())
            await asyncio.wait_for(done.wait(), 5)

    assert sleeps == [1.0, 2.0]
    assert caplog.text.count("Waiting for notifications failed") == 2


@pytest.mark.asyncio
async def test_subscribe_listener_error_is_isolated(caplog) -> None:
    """Test that a failing listener does not affect the others."""
    api = FakeNotificationAPI([[_thermostat("a")]])
    done = asyncio.Event()

    def bad_listener(_thermostat: Thermostat) -> None:
        msg = "listener failed"
        raise ValueError(msg)

    async with OJMicroline(api=api) as client:
        client.subscribe(bad_listener)
        client.subscribe(lambda _thermostat: done.set())
        await asyncio.wait_for(done.wait(), 5)

    assert "Error in notification listener" in caplog.text


@pytest.mark.asyncio
async def test_subscribe_unsupported() -> None:
    """Test that subscribing fails for an API without push notifications."""
    api = WG4API(host="ojmicroline.test.host", username="py", password="test")
    api.supports_notifications = False
    async with OJMicroline(api=api) as client:
        with pytest.raises(OJMicrolineError):
            client.subscribe(lambda _thermostat: None)
        assert client._OJMicroline__notification_task is None


@pytest.mark.asyncio
async def test_close_stops_notifications() -> None:
    """Test that closing the client cancels the notification task."""
    api = FakeNotificationAPI([])
    client = OJMicroline(api=api)
    client.subscribe(lambda _thermostat: None)
    task = client._OJMicroline__notification_task
    assert task is not None

    await client.close()

    assert task.cancelled()
    assert client._OJMicroline__notification_task is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error"),
    [(401, OJMicrolineUnauthorizedError), (500, OJMicrolineConnectionError)],
)
async def test_http_error_status(
    aresponses: ResponsesMockServer, status: int, error: type[Exception]
) -> None:
    """Test a 401 is reported as unauthorized and other errors as connection."""
    aresponses.add("ojmicroline.test.host", "/test", "GET", Response(status=status))
    async with OJMicroline(
        api=WG4API(host="ojmicroline.test.host", username="py", password="test"),
    ) as client:
        with pytest.raises(error):
            await client._request("test")


@pytest.mark.asyncio
async def test_subscribe_invalidates_rejected_session() -> None:
    """Test a rejected session is invalidated before the next wait."""
    api = FakeNotificationAPI(
        [OJMicrolineUnauthorizedError("expired"), [_thermostat("a")]]
    )
    done = asyncio.Event()

    async def fake_sleep(_delay: float) -> None:
        return

    with patch("ojmicroline_thermostat.ojmicroline.asyncio.sleep", fake_sleep):
        async with OJMicroline(api=api) as client:
            client.subscribe(lambda _thermostat: done.set())
            await asyncio.wait_for(done.wait(), 5)

    assert api.invalidations == 1
