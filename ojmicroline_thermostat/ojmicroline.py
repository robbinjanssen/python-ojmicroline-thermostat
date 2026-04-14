# pylint: disable=assignment-from-no-return
"""Asynchronous Python client communicating with the OJ Microline API."""

from __future__ import annotations

import json
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol, Self

import async_timeout
from aiohttp import ClientError, ClientSession, hdrs
from yarl import URL

from .const import COMFORT_DURATION
from .exceptions import (
    OJMicrolineAuthError,
    OJMicrolineConnectionError,
    OJMicrolineError,
    OJMicrolineTimeoutError,
)

if TYPE_CHECKING:
    from .models import Thermostat

RequestFunc = Callable[..., Awaitable[Any]]


class OJMicrolineAPI(Protocol):
    """Implements support for a specific OJ Microline API.

    OJ Microline offers various models of thermostats; somewhat unusually,
    they are managed via distinct APIs that are only vaguely similar.
    Each implementation of the OJMicrolineAPI protocol supports a group
    of thermostat models.
    """

    host: str
    """The host to use for API requests, without the protocol prefix."""

    request: RequestFunc
    """Callable for making HTTP requests, set by OJMicroline."""

    async def login(self) -> None:
        """Perform authentication against the API."""

    async def get_thermostats(self) -> list[Thermostat]:
        """Fetch all thermostats."""

    async def get_energy_usage(self, resource: Thermostat) -> list[float]:
        """Fetch energy usage for a thermostat.

        Args:
        ----
            resource: The Thermostat model.

        """

    async def set_regulation_mode(
        self,
        resource: Thermostat,
        regulation_mode: int,
        temperature: int | None,
        duration: int,
    ) -> bool:
        """Set the regulation mode on a thermostat.

        Args:
        ----
            resource: The Thermostat model.
            regulation_mode: The mode to set the thermostat to.
            temperature: The temperature to set or None.
            duration: The duration in minutes (comfort mode only).

        """


class SessionOJMicrolineAPI:
    """Base class for session-based OJ Microline APIs (WD5, WG4).

    Provides login/get_thermostats/get_energy_usage/set_regulation_mode
    implementations that use a session ID obtained via a login endpoint.
    Subclasses provide the low-level details (paths, bodies, parsers).
    """

    host: str
    request: RequestFunc

    # Subclass attributes:
    login_path: str
    get_thermostats_path: str
    get_energy_usage_path: str
    update_regulation_mode_path: str

    # Session state:
    _session_id: str | None = None
    _session_calls_left: int = 0
    _session_calls: int = 300

    def login_body(self) -> dict[str, Any]:
        """Compute HTTP body parameters used when posting to the login path."""
        raise NotImplementedError

    def get_thermostats_params(self) -> dict[str, Any]:
        """Compute additional query string params for fetching thermostats."""
        raise NotImplementedError

    def parse_thermostats_response(self, data: Any) -> list[Thermostat]:
        """Parse an HTTP response containing thermostat data."""
        raise NotImplementedError

    def parse_energy_usage_response(self, data: Any) -> list[float]:
        """Parse an HTTP response containing energy usage data."""
        raise NotImplementedError

    def update_regulation_mode_params(self, thermostat: Thermostat) -> dict[str, Any]:
        """Compute additional query string params for posting to the update path."""
        raise NotImplementedError

    def update_regulation_mode_body(
        self,
        thermostat: Thermostat,
        regulation_mode: int,
        temperature: int | None,
        duration: int,
    ) -> dict[str, Any]:
        """Compute HTTP body parameters used when posting to the update path."""
        raise NotImplementedError

    def parse_update_regulation_mode_response(self, data: Any) -> bool:
        """Parse the HTTP response received after updating the regulation mode."""
        raise NotImplementedError

    async def login(self) -> None:
        """Get a valid session to do requests with the OJ Microline API.

        Raises
        ------
            OJMicrolineAuthError: An error occurred while authenticating.

        """
        self._session_calls_left -= 1
        if self._session_calls_left < 0 or self._session_id is None:
            data = await self.request(
                self.login_path,
                method=hdrs.METH_POST,
                body=self.login_body(),
            )

            if data["ErrorCode"] == 1:
                msg = "Unable to create session, wrong username, password, API key or customer ID provided."  # noqa: E501
                raise OJMicrolineAuthError(msg)

            self._session_calls_left = self._session_calls
            self._session_id = data["SessionId"]

    async def get_thermostats(self) -> list[Thermostat]:
        """Get all the thermostats.

        Returns
        -------
            A list of Thermostats objects.

        """
        data = await self.request(
            self.get_thermostats_path,
            method=hdrs.METH_GET,
            params={
                "sessionid": self._session_id,
                **self.get_thermostats_params(),
            },
        )
        thermostats = self.parse_thermostats_response(data)

        for thermostat in thermostats:
            thermostat.energy = await self.get_energy_usage(thermostat)

        return thermostats

    async def get_energy_usage(self, resource: Thermostat) -> list[float]:
        """Get the energy usage for the provided thermostat."""
        date_tomorrow = (datetime.now(tz=UTC) + timedelta(days=1)).strftime("%Y-%m-%d")

        if self.get_energy_usage_path == "":
            return []

        data = await self.request(
            self.get_energy_usage_path,
            method=hdrs.METH_POST,
            params={
                "sessionid": self._session_id,
            },
            body={
                **self.get_thermostats_params(),
                "ThermostatID": resource.serial_number,
                "ViewType": 2,
                "DateTime": date_tomorrow,
                "History": 0,
            },
        )

        return self.parse_energy_usage_response(data)

    async def set_regulation_mode(
        self,
        resource: Thermostat,
        regulation_mode: int,
        temperature: int | None,
        duration: int,
    ) -> bool:
        """Set the regulation mode."""
        data = await self.request(
            self.update_regulation_mode_path,
            method=hdrs.METH_POST,
            params={
                "sessionid": self._session_id,
                **self.update_regulation_mode_params(resource),
            },
            body=self.update_regulation_mode_body(
                resource,
                regulation_mode,
                temperature,
                duration,
            ),
        )

        if not self.parse_update_regulation_mode_response(data):
            msg = "Unable to set preset mode."
            raise OJMicrolineError(msg)

        return True


@dataclass
class OJMicroline:
    """Main class for handling data from OJ Microline API."""

    __api: OJMicrolineAPI

    __request_timeout: float = 30.0
    __http_session: ClientSession | None = None
    __close_http_session: bool = False

    def __init__(
        self, api: OJMicrolineAPI, session: ClientSession | None = None
    ) -> None:
        """Create a new OJMicroline instance.

        Args:
        ----
            api: An object that specifies how to interact with the API.
            session: The session to use, or a new session will be created.

        """
        self.__api = api
        self.__api.request = self._request
        self.__http_session = session

    async def _request(  # pylint: disable=too-many-arguments
        self,
        uri: str,
        *,
        method: str = hdrs.METH_GET,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Handle a request to the OJ Microline API.

        Args:
        ----
            uri: Request URI, without '/', for example, 'status'
            method: HTTP method to use, for example, 'GET'
            params: Extra options to improve or limit the response.
            body: Data can be used in a POST and PATCH request.
            headers: Additional HTTP headers to include.

        Returns:
        -------
            A Python dictionary (text) with the response from
            the API.

        Raises:
        ------
            OJMicrolineTimeoutError: A timeout occurred.
            OJMicrolineConnectionError: An error occurred.
            OJMicrolineError: Received an unexpected response from the API.

        """
        try:
            if self.__http_session is None:
                self.__http_session = ClientSession()
                self.__close_http_session = True

            url = URL.build(scheme="https", host=self.__api.host, path="/").join(
                URL(uri)
            )

            request_headers = {
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
            }
            if headers:
                request_headers.update(headers)

            async with async_timeout.timeout(self.__request_timeout):
                response = await self.__http_session.request(
                    method,
                    url,
                    params=params,
                    headers=request_headers,
                    json=body,
                    ssl=True,
                )
                response.raise_for_status()
        except TimeoutError as exception:
            msg = "Timeout occurred while connecting to the OJ Microline API."
            raise OJMicrolineTimeoutError(msg) from exception
        except (ClientError, socket.gaierror) as exception:
            msg = "Error occurred while communicating with the OJ Microline API."
            raise OJMicrolineConnectionError(msg) from exception

        content_type = response.headers.get("Content-Type", "")
        if not any(item in content_type for item in ["application/json"]):
            text = await response.text()
            msg = "Unexpected content type response from the OJ Microline API"
            raise OJMicrolineError(
                msg, {"Content-Type": content_type, "response": text}
            )

        return json.loads(await response.text())

    async def login(self) -> None:
        """Log in to the OJ Microline API.

        Raises
        ------
            OJMicrolineAuthError: An error occurred while authenticating.

        """
        await self.__api.login()

    async def get_thermostats(self) -> list[Thermostat]:
        """Get all the thermostats.

        Returns
        -------
            A list of Thermostats objects.

        """
        await self.login()
        return await self.__api.get_thermostats()

    async def get_energy_usage(self, resource: Thermostat) -> list[float]:
        """Get the energy usage.

        Get the energy usage for the provided thermostat.

        Args:
        ----
            resource: The Thermostat model.

        Returns:
        -------
            A list with the energy usage for the current day and the six previous days.

        Raises:
        ------
            OJMicrolineError: An error occurred while fetching the energy usage.

        """
        await self.login()
        return await self.__api.get_energy_usage(resource)

    async def set_regulation_mode(
        self,
        resource: Thermostat,
        regulation_mode: int,
        temperature: int | None = None,
        duration: int = COMFORT_DURATION,
    ) -> bool:
        """Set the regulation mode.

        Set the provided thermostat to a given preset mode and
        temperature. The input can be a Thermostat model
        or a string containing the serial number.

        Args:
        ----
            resource: The Thermostat model.
            regulation_mode: The mode to set the thermostat to.
            temperature: The temperature to set or None.
            duration: The duration in minutes to set the temperature
                      for (comfort mode only), defaults to 4 hours.

        Returns:
        -------
            True if it succeeded.

        Raises:
        ------
            OJMicrolineError: An error occurred while setting the regulation mode.

        """
        await self.login()
        return await self.__api.set_regulation_mode(
            resource, regulation_mode, temperature, duration
        )

    async def close(self) -> None:
        """Close open client session."""
        if self.__http_session and self.__close_http_session:
            self.__close_http_session = False
            await self.__http_session.close()

    async def __aenter__(self) -> Self:
        """Async enter.

        Returns
        -------
            The API object.

        """
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        """Async exit.

        Args:
        ----
            _exc_info: Exec type.

        """
        await self.close()
