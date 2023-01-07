"""Schedule model for OJ Microline Thermostat."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


@dataclass
class ScheduleEvent:
    """Object representing a single event in a schedule."""

    date: datetime
    temperature: int

    def __init__(self, date: datetime, temperature: int) -> None:
        """
        Create a new ScheduleEvent instance.

        Args:
            date: The date of the event.
            temperature: The temperature of the event.
        """
        self.date = date
        self.temperature = temperature


@dataclass
class Schedule:
    """Object representing a schedule from a thermostat group."""

    days: dict[int, list[ScheduleEvent]]

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Schedule:
        """
        Return a new Schedule instance based on the given JSON.

        Args:
            data: The JSON data from the API.

        Returns:
            A Schedule Object.
        """

        result = {}
        now = datetime.now()
        current_day = now.weekday()
        for item in data["Days"]:
            # Define the week days.
            if item["WeekDayGrpNo"] == 0:
                day = 6
            elif item["WeekDayGrpNo"] == 1:
                day = 0
            else:
                day = item["WeekDayGrpNo"] - 1

            # Get the events.
            events = []

            for event in item["Events"]:
                if event["Active"] is True:
                    hour, minute, second = event["Clock"].split(":")

                    events.append(
                        ScheduleEvent(
                            datetime(
                                now.year,
                                now.month,
                                now.day,
                                int(hour),
                                int(minute),
                                int(second),
                                now.microsecond,
                                now.tzinfo,
                            )
                            + timedelta(days=(day - current_day)),
                            event["Temperature"],
                        )
                    )

            result[day] = events

        return cls(days=result)

    def get_active_temperature(self) -> int:
        """
        Get the current active temperature.

        Returns the currently active temperature based on the
        schedule. It parses the schedule and compares the
        schedule times with the current date time.

        Returns:
            The currently active temperature based on the schedule.
        """
        now = datetime.now()
        current_day = now.weekday()

        temperature = 0
        for event in self.days[current_day]:
            if now > event.date:
                temperature = event.temperature

        # Check if a temperature is set, if not we need yesterday
        if temperature != 0:
            return temperature

        if current_day == 0:
            prev_day = 6
        else:
            prev_day = current_day - 1

        return self.days[prev_day][-1].temperature

    def get_lowest_temperature(self) -> int:
        """
        Get the lowest temperature.

        Returns the lowest temperature based on the
        schedule. It is used for eco mode.

        Returns:
            The lowest temperature based on the schedule.
        """
        temperature = None
        for _, day in self.days.items():
            for event in day:
                if temperature is None or temperature > event.temperature:
                    temperature = event.temperature

        if temperature is None:
            return 0

        return temperature
