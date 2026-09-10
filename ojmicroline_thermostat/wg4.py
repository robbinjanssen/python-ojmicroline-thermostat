# ruff: noqa: PERF401
"""Implementation of OJMicrolineAPI for WG4-series thermostats."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from aiohttp import hdrs

from .const import REGULATION_COMFORT, REGULATION_MANUAL
from .models import Thermostat
from .ojmicroline import SessionOJMicrolineAPI


@dataclass
class WG4API(SessionOJMicrolineAPI):
    """Controls OJ Microline WG4-series thermostats (UWG4, AWG4, etc.)."""

    def __init__(
        self,
        username: str,
        password: str,
        host: str = "mythermostat.info",
        application: int = 2,
    ) -> None:
        """Create a new instance of the API object.

        Args:
        ----
            username: The username to log in with.
            password: The password for the username.
            host: The host name used for API requests.
            application: The application code sent on login. Defaults to 2.
                Some white-labelled clouds scope thermostats to a different
                code (e.g. Danfoss LX on lxwifi.danfoss.us uses 4); with the
                wrong code the thermostat list is returned empty.

        """
        self.username = username
        self.password = password
        self.host = host
        self.application = application

    login_path: str = "api/authenticate/user"

    def login_body(self) -> dict[str, Any]:  # noqa: D102
        return {
            "Application": self.application,
            "Confirm": "",
            "Email": self.username,
            "Password": self.password,
        }

    get_thermostats_path: str = "api/thermostats"

    def get_thermostats_params(self) -> dict[str, Any]:  # noqa: D102
        return {}

    def parse_thermostats_response(self, data: Any) -> list[Thermostat]:  # noqa: D102
        results: list[Thermostat] = []
        for group in data["Groups"]:
            for item in group["Thermostats"]:
                if len(item):
                    results.append(Thermostat.from_wg4_json(item))

        return results

    get_energy_usage_path: str = ""

    def parse_energy_usage_response(self, data: Any) -> list[float]:  # noqa: D102,ARG002
        return []

    update_regulation_mode_path: str = "api/thermostat"

    def update_regulation_mode_params(  # noqa: D102
        self, thermostat: Thermostat
    ) -> dict[str, Any]:
        return {"serialnumber": thermostat.serial_number}

    def update_regulation_mode_body(  # noqa: D102
        self,
        thermostat: Thermostat,
        regulation_mode: int,
        temperature: int | None,
        duration: int,
    ) -> dict[str, Any]:
        extras: dict[str, Any] = {}
        if regulation_mode == REGULATION_MANUAL:
            extras = {
                "ManualTemperature": temperature,
            }
        elif regulation_mode == REGULATION_COMFORT:
            end = datetime.now(tz=UTC) + timedelta(minutes=duration)
            extras = {
                "ComfortTemperature": temperature,
                "ComfortEndTime": end.strftime("%d/%m/%Y %H:%M:00 +00:00"),
            }

        return {
            "RegulationMode": regulation_mode,
            "VacationEnabled": thermostat.vacation_mode,
            **extras,
        }

    def parse_update_regulation_mode_response(self, data: Any) -> bool:  # noqa: D102
        return data["Success"]

    supports_notifications: bool = True

    notification_path: str = "api/notification"

    # Client-side timeout for a notification request. The server holds the
    # request open for roughly a minute before answering that nothing
    # changed, so this must be comfortably longer than that.
    notification_timeout: float = 120.0

    _subscribed_session_id: str | None = None

    async def get_notifications(self) -> list[Thermostat]:
        """Wait for the next push notification about a thermostat.

        The WG4 API delivers notifications by long-polling: a request to the
        notification path blocks until a thermostat changes, or until the
        server gives up after about a minute and answers with no thermostat.
        Notifications are queued per session, and only after that session has
        fetched the thermostat list, so the first call after a (re)login
        fetches and returns every thermostat to (re)subscribe the session.

        Returns
        -------
            A list with the changed thermostat, every thermostat after
            (re)subscribing, or nothing if no change occurred.

        """
        if self._subscribed_session_id != self._session_id:
            thermostats = await self.get_thermostats()
            self._subscribed_session_id = self._session_id
            return thermostats

        data = await self.request(
            self.notification_path,
            method=hdrs.METH_GET,
            params={"sessionid": self._session_id},
            request_timeout=self.notification_timeout,
        )

        # A timed-out wait is reported as Action 0 with a null Thermostat;
        # a change is Action 2 with the full thermostat payload.
        if not data.get("Thermostat"):
            return []

        thermostat = Thermostat.from_wg4_json(data["Thermostat"])
        thermostat.energy = await self.get_energy_usage(thermostat)
        return [thermostat]
