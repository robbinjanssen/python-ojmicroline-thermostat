"""GroupRequest model for OJ Microline Thermostat."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..const import (
    REGULATION_MANUAL,
    REGULATION_COMFORT,
    REGULATION_VACATION,
    REGULATION_SCHEDULE,
    REGULATION_BOOST,
)
from ..models import Thermostat


@dataclass
class UpdateGroupRequest:
    """UpdateGroupRequest instance."""

    thermostat: Thermostat
    api_key: str

    def __init__(cls, thermostat: Thermostat, api_key: str):
        """
        Initialise a new UpdateGroupRequest instance based on the given arguments.

        Args:
            thermostat: The thermostat to update.
            api_key: The API key to use in the request.
        """

        cls.thermostat = thermostat
        cls.api_key = api_key

    def update_regulation_mode(
        self,
        regulation_mode: int,
        temperature: int | None = None
    ):
        """
        Return a request body to update the regulation mode for a group.

        Args:
            regulation_mode: The mode to set the regulation mode in.
            temperature: The temperature (for comfort and manual mode) or None.

        Returns:
            A JSON object containing the update.
        """
        if temperature is not None:
            self.thermostat.temperatures[regulation_mode] = temperature

        if regulation_mode == REGULATION_COMFORT:
            comfort_end_time = (datetime.today() + timedelta(hours=4))
        else:
            comfort_end_time = self.thermostat.dates["comfort_end_time"]

        if regulation_mode == REGULATION_BOOST:
            boost_end_time = (datetime.today() + timedelta(hours=1))
        else:
            boost_end_time = self.thermostat.dates["boost_end_time"]

        return {
            "APIKEY": self.api_key,
            "SetGroup": {
                "ExcludeVacationData": False,
                "GroupId": self.thermostat.zone_id,
                "GroupName": self.thermostat.zone_name,
                "BoostEndTime": boost_end_time.strftime("%Y-%m-%dT%H:%M:%S"),
                "ComfortEndTime": comfort_end_time.strftime("%Y-%m-%dT%H:%M:%S"),
                "ComfortSetpoint": self.thermostat.temperatures[REGULATION_COMFORT],
                "LastPrimaryModeIsAuto": (self.thermostat.regulation_mode == REGULATION_SCHEDULE),  # noqa: E501
                "ManualModeSetpoint": self.thermostat.temperatures[REGULATION_MANUAL],
                "RegulationMode": regulation_mode,
                "Schedule": self.thermostat.schedule,
                "VacationEnabled": self.thermostat.vacation_enabled,
                "VacationBeginDay": self.thermostat.dates["vacation_begin_time"].strftime("%Y-%m-%dT%H:%M:%S"),  # noqa: E501
                "VacationEndDay": self.thermostat.dates["vacation_end_time"].strftime("%Y-%m-%dT%H:%M:%S"),  # noqa: E501
                "VacationTemperature": self.thermostat.temperatures[REGULATION_VACATION],  # noqa: E501
            }
        }
