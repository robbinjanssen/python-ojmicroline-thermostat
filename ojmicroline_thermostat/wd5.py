# ruff: noqa: PLR0913, PERF401
# pylint: disable=too-many-arguments
"""Implementation of OJMicrolineAPI for WD5-series thermostats."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .const import (
    REGULATION_BOOST,
    REGULATION_COMFORT,
    REGULATION_MANUAL,
    REGULATION_SCHEDULE,
    WD5_DATETIME_FORMAT,
)
from .exceptions import OJMicrolineResultsError
from .models import Thermostat
from .ojmicroline import OJMicrolineAPI


@dataclass
class WD5API(OJMicrolineAPI):
    """Controls OJ Microline WD5-series thermostats (OWD5, MWD5, etc.)."""

    def __init__(
        self,
        api_key: str,
        customer_id: int,
        username: str,
        password: str,
        client_sw_version: int = 1060,
        host: str = "ocd5.azurewebsites.net",
    ) -> None:
        """Create a new instance of the API object.

        Args:
        ----
            api_key: The API key to use in requests.
            customer_id: The customer ID to use in requests.
            username: The username to log in with.
            password: The password for the username.
            client_sw_version: The client software version.
            host: The host name used for API requests.

        """
        self.api_key = api_key
        self.customer_id = customer_id
        self.username = username
        self.password = password
        self.client_sw_version = client_sw_version
        self.host = host

    login_path: str = "api/UserProfile/SignIn"

    def login_body(self) -> dict[str, Any]:  # noqa: D102
        return {
            "APIKEY": self.api_key,
            "UserName": self.username,
            "Password": self.password,
            "ClientSWVersion": self.client_sw_version,
            "CustomerId": self.customer_id,
        }

    get_thermostats_path: str = "api/Group/GroupContents"

    def get_thermostats_params(self) -> dict[str, Any]:  # noqa: D102
        return {"APIKEY": self.api_key}

    def parse_thermostats_response(self, data: Any) -> list[Thermostat]:  # noqa: D102
        if data["ErrorCode"] == 1:
            msg = "Unable to get thermostats via API."
            raise OJMicrolineResultsError(msg, {"data": data})

        results: list[Thermostat] = []
        for group in data["GroupContents"]:
            for item in group["Thermostats"]:
                if len(item):
                    results.append(Thermostat.from_wd5_json(item))
        return results

    update_regulation_mode_path: str = "api/Group/UpdateGroup"

    def update_regulation_mode_params(  # noqa: D102
        self,
        thermostat: Thermostat,  # noqa: ARG002
    ) -> dict[str, Any]:
        return {}

    def update_regulation_mode_body(  # noqa: D102
        self,
        thermostat: Thermostat,
        regulation_mode: int,
        temperature: int | None,
        duration: int,
    ) -> dict[str, Any]:
        if regulation_mode == REGULATION_COMFORT:
            comfort_end_time = (
                datetime.now(tz=UTC) + timedelta(minutes=duration)
            ).astimezone()
            comfort_temperature = temperature or thermostat.comfort_temperature
        else:
            comfort_end_time = thermostat.comfort_end_time
            comfort_temperature = thermostat.comfort_temperature

        if regulation_mode == REGULATION_BOOST:
            boost_end_time: datetime | None = (
                datetime.now(tz=UTC) + timedelta(hours=1)
            ).astimezone()
        else:
            boost_end_time = thermostat.boost_end_time

        if regulation_mode == REGULATION_MANUAL:
            manual_temperature = temperature or thermostat.manual_temperature
        else:
            manual_temperature = thermostat.manual_temperature

        return {
            "APIKEY": self.api_key,
            "SetGroup": {
                "ExcludeVacationData": False,
                "GroupId": thermostat.zone_id,
                "GroupName": thermostat.zone_name,
                "BoostEndTime": _fmt(boost_end_time),
                "ComfortEndTime": _fmt(comfort_end_time),
                "ComfortSetpoint": comfort_temperature,
                "LastPrimaryModeIsAuto": (
                    thermostat.regulation_mode == REGULATION_SCHEDULE
                ),
                "ManualModeSetpoint": manual_temperature,
                "RegulationMode": regulation_mode,
                "Schedule": thermostat.schedule,
                "VacationEnabled": thermostat.vacation_mode,
                "VacationBeginDay": _fmt(thermostat.vacation_begin_time),
                "VacationEndDay": _fmt(thermostat.vacation_end_time),
                "VacationTemperature": thermostat.vacation_temperature,
            },
        }

    def parse_update_regulation_mode_response(self, data: Any) -> bool:  # noqa: D102
        # "== 0" would seem more appropriate here, but as of this writing I
        # don't have a WD5-series thermostat to test with so I am preserving
        # the exact semantics of older code.
        return data["ErrorCode"] != 1


def _fmt(d: datetime | None) -> Any:
    return None if d is None else d.strftime(WD5_DATETIME_FORMAT)
