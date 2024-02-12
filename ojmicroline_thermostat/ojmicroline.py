"""Asynchronous Python client communicating with the OJ Microline API."""
from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self

import async_timeout
from aiohttp import ClientError, ClientSession, hdrs
from typing_extensions import Protocol
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


class OJMicrolineAPI(Protocol):
    """Implements support for a specific OJ Microline API.

    OJ Microline offers various models of thermostats; somewhat unusually,
    they are managed via distinct APIs that are only vaguely similar.
    Each implementation of the OJMicrolineAPI protocol supports a group
    of thermostat models.
    """

    host: str
    """The host to use for API requests, without the protocol prefix."""

    login_path: str
    """HTTP path used to log in and fetch a session id."""

    def login_body(self) -> dict[str, Any]:
        """Compute HTTP body parameters used when posting to the login path."""

    get_thermostats_path: str
    """HTTP path used to fetch a list of thermostats."""

    def get_thermostats_params(self) -> dict[str, Any]:
        """Compute additional query string params for fetching thermostats."""

    def parse_thermostats_response(self, data: Any) -> list[Thermostat]:
        """Parse an HTTP response containing thermostat data.

        Args:
        ----
            data: The JSON data contained in the response.

        """

    update_regulation_mode_path: str
    """HTTP path used to update a thermostat's mode"""

    def update_regulation_mode_params(self, thermostat: Thermostat) -> dict[str, Any]:
        """Compute additional query string params for posting to the update path.

        Args:
        ----
            thermostat: The Thermostat model.

        """

    def update_regulation_mode_body(
        self,
        thermostat: Thermostat,
        regulation_mode: int,
        temperature: int | None,
        duration: int,
    ) -> dict[str, Any]:
        """Compute HTTP body parameters used when posting to the update path.

        Args:
        ----
            thermostat: The Thermostat model.
            regulation_mode: The mode to set the thermostat to.
            temperature: The temperature to set or None.
            duration: The duration in minutes to set the temperature
                      for (comfort mode only).

        Returns: A dict with values to be used in an HTTP POST body.

        """

    def parse_update_regulation_mode_response(self, data: Any) -> bool:
        """Parse the HTTP response received after updating the regulation mode.

        Args:
        ----
            data: The JSON data contained in the response.

        Returns: True if the update succeeded.

        """


@dataclass
class OJMicroline:
    """Main class for handling data from OJ Microline API."""

    __api: OJMicrolineAPI

    __request_timeout: float = 30.0
    __http_session: ClientSession | None = None
    __close_http_session: bool = False

    # The session ID to perform calls with.
    __session_id: str | None = None

    # Reset the login session when this hits zero.
    __session_calls_left: int = 0

    # Maximum number of requests within a single session.
    __session_calls: int = 300

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
        self.__http_session = session

    async def _request(
        self,
        uri: str,
        *,
        method: str = hdrs.METH_GET,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        """Handle a request to the OJ Microline API.

        Args:
        ----
            uri: Request URI, without '/', for example, 'status'
            method: HTTP method to use, for example, 'GET'
            params: Extra options to improve or limit the response.
            body: Data can be used in a POST and PATCH request.

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

            # Reduce the number of calls left by 1.
            self.__session_calls_left -= 1
            url = URL.build(scheme="https", host=self.__api.host, path="/").join(
                URL(uri)
            )

            async with async_timeout.timeout(self.__request_timeout):
                response = await self.__http_session.request(
                    method,
                    url,
                    params=params,
                    headers={
                        "Content-Type": "application/json; charset=utf-8",
                        "Accept": "application/json",
                    },
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
        """Get a valid session to do requests with the OJ Microline API.

        Raises
        ------
            OJMicrolineAuthError: An error occurred while authenticating.

        """
        if self.__session_calls_left == 0 or self.__session_id is None:
            # Get a new session.
            data = await self._request(
                self.__api.login_path,
                method=hdrs.METH_POST,
                body=self.__api.login_body(),
            )

            if data["ErrorCode"] == 1:
                msg = "Unable to create session, wrong username, password, API key or customer ID provided."  # noqa: E501
                raise OJMicrolineAuthError(msg)

            # Reset the number of session calls.
            self.__session_calls_left = self.__session_calls
            self.__session_id = data["SessionId"]

    async def get_thermostats(self) -> list[Thermostat]:
        """Get all the thermostats.

        Returns
        -------
            A list of Thermostats objects.

        """
        await self.login()

        data = await self._request(
            self.__api.get_thermostats_path,
            method=hdrs.METH_GET,
            params={
                "sessionid": self.__session_id,
                **self.__api.get_thermostats_params(),
            },
        )
        return self.__api.parse_thermostats_response(data)

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
        if self.__session_id is None:
            await self.login()

        data = await self._request(
            self.__api.update_regulation_mode_path,
            method=hdrs.METH_POST,
            params={
                "sessionid": self.__session_id,
                **self.__api.update_regulation_mode_params(resource),
            },
            body=self.__api.update_regulation_mode_body(
                resource,
                regulation_mode,
                temperature,
                duration,
            ),
        )

        if not self.__api.parse_update_regulation_mode_response(data):
            msg = "Unable to set preset mode."
            raise OJMicrolineError(msg)

        return True

    async def close(self) -> None:
        """Close open client session."""
        if self.__http_session and self.__close_http_session:
            self.__close_http_session = False
            self.__session_id = None
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
