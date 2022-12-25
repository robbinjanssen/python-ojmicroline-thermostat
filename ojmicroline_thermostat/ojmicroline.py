"""Asynchronous Python client communicating with the OJ Microline API."""
from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import dataclass
from typing import Any

from aiohttp import ClientSession, ClientError, hdrs
import async_timeout
from yarl import URL

from .exceptions import OJMicrolineConnectionError, OJMicrolineError
from .models import Thermostat
from .requests import UpdateGroupRequest


@dataclass
class OJMicroline:
    """Main class for handling data from OJ Microline API."""

    __host: str
    __api_key: str
    __customer_id: int
    __username: str
    __password: str
    __client_sw_version: int

    __ojmicroline_session_id: str | None = None

    __request_timeout: float = 30.0
    __http_session: ClientSession | None = None
    __close_http_session: bool = False

    def __init__(
        self,
        host: str,
        api_key: str,
        customer_id: int,
        username: str,
        password: str,
        client_sw_version: int = 1060,
        session: ClientSession | None = None
    ) -> None:
        """
        Create a new OJMicroline instance.

        Args:
            host: The HOST (without protocol) to use for API requests.
            api_key: The API key to use in requests.
            customer_id: The customer ID to use in requests.
            username: The username to log in with.
            password: The password for the username.
            client_sw_version: The client software version.
            session: The session to use, or a new session will be created.
        """
        self.__host = host
        self.__api_key = api_key
        self.__customer_id = customer_id
        self.__username = username
        self.__password = password
        self.__client_sw_version = client_sw_version
        self.__http_session = session if session else ClientSession()
        self.__close_http_session = True

    async def _request(
        self,
        uri: str,
        *,
        method: str = hdrs.METH_GET,
        params: dict[str, Any] | None = None,
        body: dict | None = None,
    ) -> Any:
        """
        Handle a request to the OJ Microline API.

        Args:
            uri: Request URI, without '/', for example, 'status'
            method: HTTP method to use, for example, 'GET'
            params: Extra options to improve or limit the response.
            body: Data can be used in a POST and PATCH request.

        Returns:
            A Python dictionary (text) with the response from
            the API.

        Raises:
            OJMicrolineConnectionError: An error occurred while communicating
                                        with the API.
            OJMicrolineError: Received an unexpected response from the API.
        """
        try:
            url = URL.build(scheme="https", host=self.__host, path="/").join(
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
        except asyncio.TimeoutError as exception:
            raise OJMicrolineConnectionError(
                "Timeout occurred while connecting to the OJ Microline API.",
            ) from exception
        except (ClientError, socket.gaierror) as exception:
            raise OJMicrolineConnectionError(
                "Error occurred while communicating with the OJ Microline API."
            ) from exception

        content_type = response.headers.get("Content-Type", "")
        if not any(item in content_type for item in ["application/json"]):
            text = await response.text()
            raise OJMicrolineError(
                "Unexpected content type response from the OJ Microline API",
                {"Content-Type": content_type, "response": text},
            )

        return json.loads(await response.text())

    async def login(self) -> None:
        """
        Get a valid session to do requests with the OJ Microline API.

        Raises:
            OJMicrolineConnectionError: An error occured while logging in.
        """
        if self.__ojmicroline_session_id is None:
            """Get a new session"""
            data = await self._request(
                "api/UserProfile/SignIn",
                method=hdrs.METH_POST,
                body={
                    'APIKEY': self.__api_key,
                    'UserName': self.__username,
                    'Password': self.__password,
                    'ClientSWVersion': self.__client_sw_version,
                    'CustomerId': self.__customer_id,
                }
            )

            if data["ErrorCode"] == 1:
                raise OJMicrolineConnectionError(
                    "Unable to create session, wrong username, password, \
                        API key or customer ID provided"
                )

            self.__ojmicroline_session_id = data["SessionId"]

    async def get_thermostats(self) -> list[Thermostat]:
        """
        Get all the thermostats.

        Returns:
            A list of Thermostats objects.

        Raises:
            OJMicrolineError: An error occured while getting thermostats.
        """
        if self.__ojmicroline_session_id is None:
            await self.login()

        results: list[Thermostat] = []
        data = await self._request(
            "api/Group/GroupContents",
            method=hdrs.METH_GET,
            params={
                "sessionid": self.__ojmicroline_session_id,
                "APIKEY": self.__api_key
            },
        )

        if data["ErrorCode"] == 1:
            raise OJMicrolineError("Unable to get thermostats via API.")

        for group in data["GroupContents"]:
            for item in group["Thermostats"]:
                if len(item):
                    results.append(Thermostat.from_json(item))

        return results

    async def get_thermostat(self, serial_number: str) -> Thermostat | False:
        """
        Get a single thermostat.

        Args:
            serial_number: The serial number of the thermostat to get.

        Returns:
            A list of Thermostats objects.
        """
        thermostat = False
        data = await self.get_thermostats()

        for item in data:
            if item.serial_number == serial_number:
                thermostat = item

        return thermostat

    async def set_regulation_mode(
        self,
        resource: Thermostat | str,
        regulation_mode: int,
        temperature: int | None = None
    ) -> bool:
        """
        Set the regulation mode.

        Set the provided thermostat to a given preset mode and
        temperature. The input can be a Thermostat model
        or a string containing the serial number.

        Args:
            resource: The Thermostat model or the serial number of
                      the thermostat to update.
            regulation_mode: The mode to set the thermostat to.
            temperature: The temperature to set or None.

        Returns:
            True if it succeeded.

        Raises:
            OJMicrolineError: An error occured while setting the regulation mode.
        """
        if self.__ojmicroline_session_id is None:
            await self.login()

        if isinstance(resource, Thermostat):
            thermostat = resource
        else:
            thermostat = await self.get_thermostat(resource)

        request = UpdateGroupRequest(thermostat, self.__api_key)
        data = await self._request(
            "api/Group/UpdateGroup",
            method=hdrs.METH_POST,
            params={"sessionid": self.__ojmicroline_session_id},
            body=request.update_regulation_mode(regulation_mode, temperature)
        )

        if data["ErrorCode"] == 1:
            raise OJMicrolineError("Unable to set preset mode.")

        return True

    async def close(self) -> None:
        """Close open client session."""
        if self.__http_session and self.__close_http_session:
            await self.__http_session.close()
            self.__close_http_session = False

    async def __aenter__(self) -> OJMicroline:
        """
        Async enter.

        Returns:
            The API object.
        """
        return self

    async def __aexit__(self, *_exc_info: str) -> None:
        """
        Async exit.

        Args:
            _exc_info: Exec type.
        """
        await self.close()
