# ruff: noqa: PERF401
"""Implementation of OJMicrolineAPI for WG4-series thermostats."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .const import REGULATION_COMFORT, REGULATION_MANUAL
from .models import Thermostat
from .ojmicroline import OJMicrolineAPI


@dataclass
class WG4API(OJMicrolineAPI):
    """Controls OJ Microline WG4-series thermostats (UWG4, AWG4, etc.)."""

    def __init__(
        self, username: str, password: str, host: str = "mythermostat.info"
    ) -> None:
        """Create a new instance of the API object.

        Args:
        ----
            username: The username to log in with.
            password: The password for the username.
            host: The host name used for API requests.

        """
        self.username = username
        self.password = password
        self.host = host

    login_path: str = "api/authenticate/user"

    def login_body(self) -> dict[str, Any]:  # noqa: D102
        return {
            "Application": 2,
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
