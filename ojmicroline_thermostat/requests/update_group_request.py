"""GroupRequest model for OJ Microline Thermostat."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ..const import (
    DATETIME_FORMAT,
    REGULATION_BOOST,
    REGULATION_COMFORT,
    REGULATION_MANUAL,
    REGULATION_SCHEDULE,
    REGULATION_VACATION,
)
from ..models import Thermostat


@dataclass
class UpdateGroupRequest:
    """UpdateGroupRequest instance."""

    resource: Thermostat
    api_key: str

    def __init__(self, resource: Thermostat, api_key: str):
        """
        Initialise a new UpdateGroupRequest instance based on the given arguments.

        Args:
            resource: The thermostat to update.
            api_key: The API key to use in the request.
        """

        self.resource = resource
        self.api_key = api_key

    def update_regulation_mode(
        self, regulation_mode: int, temperature: int | None = None
    ) -> dict[str, Any]:
        """
        Return a request body to update the regulation mode for a group.

        Args:
            regulation_mode: The mode to set the regulation mode in.
            temperature: The temperature (for comfort and manual mode) or None.

        Returns:
            A JSON object containing the update.
        """
        if temperature is not None:
            self.resource.temperatures[regulation_mode] = temperature

        if regulation_mode == REGULATION_COMFORT:
            comfort_end_time = datetime.today() + timedelta(hours=4)
        else:
            comfort_end_time = self.resource.comfort_end_time

        if regulation_mode == REGULATION_BOOST:
            boost_end_time = datetime.today() + timedelta(hours=1)
        else:
            boost_end_time = self.resource.boost_end_time

        return {
            "APIKEY": self.api_key,
            "SetGroup": {
                "ExcludeVacationData": False,
                "GroupId": self.resource.zone_id,
                "GroupName": self.resource.zone_name,
                "BoostEndTime": boost_end_time.strftime(DATETIME_FORMAT),
                "ComfortEndTime": comfort_end_time.strftime(DATETIME_FORMAT),
                "ComfortSetpoint": self.resource.temperatures[REGULATION_COMFORT],
                "LastPrimaryModeIsAuto": (
                    self.resource.regulation_mode == REGULATION_SCHEDULE
                ),  # noqa: E501
                "ManualModeSetpoint": self.resource.temperatures[REGULATION_MANUAL],
                "RegulationMode": regulation_mode,
                "Schedule": self.resource.schedule,
                "VacationEnabled": self.resource.vacation_mode,
                "VacationBeginDay": self.resource.vacation_begin_time.strftime(
                    DATETIME_FORMAT
                ),  # noqa: E501
                "VacationEndDay": self.resource.vacation_end_time.strftime(
                    DATETIME_FORMAT
                ),  # noqa: E501
                "VacationTemperature": self.resource.temperatures[
                    REGULATION_VACATION
                ],  # noqa: E501
            },
        }
