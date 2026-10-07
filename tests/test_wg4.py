# pylint: disable=protected-access
# mypy: disable-error-code=attr-defined
"""Integration test for the WG4API class."""

import json
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest
from aiohttp import web
from aresponses import Response, ResponsesMockServer
from freezegun import freeze_time

from ojmicroline_thermostat import (
    WG4API,
    OJMicroline,
    OJMicrolineAuthError,
    OJMicrolineError,
    OJMicrolineResultsError,
    Thermostat,
)
from ojmicroline_thermostat.const import (
    REGULATION_COMFORT,
    REGULATION_MANUAL,
    REGULATION_SCHEDULE,
)

from . import load_fixtures


@pytest.mark.asyncio
async def test_login(aresponses: ResponsesMockServer) -> None:
    """Test the login method."""
    aresponses.add(
        "ojmicroline.test.host",
        "/api/authenticate/user",
        "POST",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"SessionId": "f00br4", "ErrorCode": 0}),
        ),
    )
    async with aiohttp.ClientSession() as session:
        api = WG4API(
            host="ojmicroline.test.host",
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
        "/api/authenticate/user",
        "POST",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"ErrorCode": 1}),
        ),
    )
    async with aiohttp.ClientSession() as session:
        api = WG4API(
            host="ojmicroline.test.host",
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
        "/api/thermostats",
        "GET",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=load_fixtures("wg4_group.json"),
        ),
    )
    add_energy_usage_response(aresponses)
    async with aiohttp.ClientSession() as session:
        api = WG4API(
            host="ojmicroline.test.host",
            username="py",
            password="test",
        )
        api._session_calls_left = 300
        api._session_id = "f00b4r"
        client = OJMicroline(api=api, session=session)

        thermostats: list[Thermostat] = await client.get_thermostats()

        assert thermostats is not None
        assert len(thermostats) > 0
        for item in thermostats:
            assert item.serial_number is not None
            assert item.energy is not None
            assert len(item.energy) == 7


@pytest.mark.asyncio
async def test_set_regulation_mode(aresponses: ResponsesMockServer) -> None:
    """Test updating the regulation mode."""
    aresponses.add(
        "ojmicroline.test.host",
        "/api/thermostat",
        "POST",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"Success": True}),
        ),
    )
    async with aiohttp.ClientSession() as session:
        data = load_fixtures("wg4_thermostat.json")
        thermostat = Thermostat.from_wg4_json(json.loads(data))

        api = WG4API(
            host="ojmicroline.test.host",
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
        "/api/thermostat",
        "POST",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"Success": True}),
        ),
    )
    async with aiohttp.ClientSession() as session:
        api = WG4API(
            host="ojmicroline.test.host",
            username="py",
            password="test",
        )

        data = load_fixtures("wg4_thermostat.json")
        thermostat = Thermostat.from_wg4_json(json.loads(data))

        def set_session_id() -> None:
            api._session_id = "f00b4r"

        with patch.object(
            OJMicroline, "login", side_effect=set_session_id
        ) as mock_login:
            client = OJMicroline(api=api, session=session)
            await client.set_regulation_mode(thermostat, REGULATION_SCHEDULE)

        mock_login.assert_called_once()


@pytest.mark.asyncio
async def test_set_regulation_mode_failed(aresponses: ResponsesMockServer) -> None:
    """Test update the regulation mode when an error occurs."""
    aresponses.add(
        "ojmicroline.test.host",
        "/api/thermostat",
        "POST",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"Success": False}),
        ),
    )
    async with aiohttp.ClientSession() as session:
        data = load_fixtures("wg4_thermostat.json")
        thermostat = Thermostat.from_wg4_json(json.loads(data))

        api = WG4API(
            host="ojmicroline.test.host",
            username="py",
            password="test",
        )
        api._session_calls_left = 300
        api._session_id = "f00b4r"
        client = OJMicroline(api=api, session=session)

        with pytest.raises(OJMicrolineError):
            await client.set_regulation_mode(thermostat, REGULATION_COMFORT, 2500, 360)


def test_parse_energy_usage_response() -> None:
    """Test that the days of all weeks in the response are returned in order."""
    api = WG4API(
        host="ojmicroline.test.host",
        username="py",
        password="test",
    )
    data = json.loads(load_fixtures("wg4_energy.json"))

    assert api.parse_energy_usage_response(data) == [
        0.0,
        0.0,
        0.0,
        0.0,
        1.2,
        2.5,
        3.1,
        4.2,
        5.3,
        6.4,
        7.5,
        8.6,
        9.7,
        10.8,
    ]


@pytest.mark.parametrize("data", [{}, {"EnergyUsage": []}, {"Message": "Error"}])
def test_parse_energy_usage_response_failed(data: dict[str, Any]) -> None:
    """Test that a response without energy usage raises an error."""
    api = WG4API(
        host="ojmicroline.test.host",
        username="py",
        password="test",
    )

    with pytest.raises(OJMicrolineResultsError):
        api.parse_energy_usage_response(data)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("now", "today", "expected"),
    [
        # Monday evening in the thermostat's time zone, but already
        # Tuesday in UTC:
        ("2026-10-06 03:00:00", "2026-10-05", [2.5, 3.1, 4.2, 5.3, 6.4, 7.5, 8.6]),
        # Sunday, the first day of the week:
        ("2026-10-04 12:00:00", "2026-10-04", [3.1, 4.2, 5.3, 6.4, 7.5, 8.6, 9.7]),
        # Saturday, the last day of the week:
        ("2026-10-10 12:00:00", "2026-10-10", [0.0, 0.0, 0.0, 0.0, 1.2, 2.5, 3.1]),
    ],
)
async def test_get_energy_usage(
    aresponses: ResponsesMockServer, now: str, today: str, expected: list[float]
) -> None:
    """Test that the usage of today and the six previous days is returned."""

    def handler(request: web.Request) -> web.Response:
        assert request.query["sessionid"] == "f00b4r"
        assert request.query["serialnumber"] == "42424242"
        assert request.query["view"] == "week"
        assert request.query["date"] == today
        assert request.query["history"] == "1"
        assert request.query["weekstart"] == "sunday"
        return Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=load_fixtures("wg4_energy.json"),
        )

    aresponses.add("ojmicroline.test.host", "/api/energyusage", "GET", handler)
    async with aiohttp.ClientSession() as session:
        api = WG4API(
            host="ojmicroline.test.host",
            username="py",
            password="test",
        )
        api._session_calls_left = 300
        api._session_id = "f00b4r"
        OJMicroline(api=api, session=session)

        thermostat = Thermostat.from_wg4_json(
            json.loads(load_fixtures("wg4_thermostat.json"))
        )
        thermostat.utc_offset = timedelta(hours=-4)

        with freeze_time(now):
            assert await api.get_energy_usage(thermostat) == expected


def test_login_body_default_application() -> None:
    """The WG4 login body defaults to Application code 2."""
    api = WG4API(host="ojmicroline.test.host", username="py", password="test")
    assert api.login_body()["Application"] == 2


def test_login_body_custom_application() -> None:
    """A custom application code (e.g. 4 for Danfoss LX) is sent on login."""
    api = WG4API(
        host="lxwifi.danfoss.us",
        username="py",
        password="test",
        application=4,
    )
    assert api.login_body()["Application"] == 4


@pytest.mark.asyncio
async def test_get_notifications_subscribes_new_session(
    aresponses: ResponsesMockServer,
) -> None:
    """Test that a session is subscribed by fetching thermostats first."""
    aresponses.add(
        "ojmicroline.test.host",
        "/api/thermostats",
        "GET",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=load_fixtures("wg4_group.json"),
        ),
    )
    add_energy_usage_response(aresponses)
    async with aiohttp.ClientSession() as session:
        api = WG4API(
            host="ojmicroline.test.host",
            username="py",
            password="test",
        )
        api._session_id = "f00b4r"
        OJMicroline(api=api, session=session)

        assert api.supports_notifications is True
        thermostats = await api.get_notifications()

        assert len(thermostats) > 0
        assert api._subscribed_session_id == "f00b4r"


@pytest.mark.asyncio
async def test_get_notifications_update(aresponses: ResponsesMockServer) -> None:
    """Test that a notification is parsed into a thermostat."""
    aresponses.add(
        "ojmicroline.test.host",
        "/api/notification",
        "GET",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=load_fixtures("wg4_notification.json"),
        ),
    )
    async with aiohttp.ClientSession() as session:
        api = WG4API(
            host="ojmicroline.test.host",
            username="py",
            password="test",
        )
        api._session_id = "f00b4r"
        api._subscribed_session_id = "f00b4r"
        OJMicroline(api=api, session=session)

        thermostats = await api.get_notifications()

        assert len(thermostats) == 1
        assert thermostats[0].serial_number == "42424242"
        assert thermostats[0].energy is None


@pytest.mark.asyncio
async def test_get_notifications_no_change(aresponses: ResponsesMockServer) -> None:
    """Test that a timed-out wait yields no thermostats."""
    aresponses.add(
        "ojmicroline.test.host",
        "/api/notification",
        "GET",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"SequenceNr": 3, "Action": 0, "Thermostat": None}),
        ),
    )
    async with aiohttp.ClientSession() as session:
        api = WG4API(
            host="ojmicroline.test.host",
            username="py",
            password="test",
        )
        api._session_id = "f00b4r"
        api._subscribed_session_id = "f00b4r"
        OJMicroline(api=api, session=session)

        assert await api.get_notifications() == []


def add_energy_usage_response(aresponses: ResponsesMockServer) -> None:
    """Respond to any number of energy usage requests."""
    aresponses.add(
        "ojmicroline.test.host",
        "/api/energyusage",
        "GET",
        Response(
            status=200,
            headers={"Content-Type": "application/json"},
            text=load_fixtures("wg4_energy.json"),
        ),
        repeat=aresponses.INFINITY,
    )
