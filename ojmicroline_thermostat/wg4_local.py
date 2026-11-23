"""Implementation of OJMicrolineAPI for WG4-series thermostats on a local gateway."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .const import REGULATION_MANUAL, REGULATION_SCHEDULE
from .exceptions import OJMicrolineError, OJMicrolineScheduleError
from .gateway import WG4Gateway
from .gateway.wg4 import check_days, check_events
from .local import VOLATILE_FIELDS, LocalAPI
from .models import Thermostat
from .models.thermostat import parse_wg4_load
from .wg4 import WG4API

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from datetime import time

# The regulation modes the gateway can set by itself.
LOCAL_MODES = (REGULATION_SCHEDULE, REGULATION_MANUAL)


class WG4LocalAPI(LocalAPI[WG4API, WG4Gateway]):
    """Controls WG4-series thermostats through a local gateway.

    A thermostat that connects to the gateway reports to this API directly and
    takes its commands from it. The cloud API still supplies what a thermostat
    does not report itself, such as its temperature limits, and it serves the
    thermostats of the account that never connected to the gateway.

    The last answer of the cloud API is kept as a snapshot, so the thermostats
    on the gateway keep working when the cloud cannot be reached. Pass the
    stored snapshot in, and store the one on_snapshot is called with. A
    snapshot holds the email address of the account.

    The schedule and manual modes go straight to a thermostat on the gateway.
    Other modes go through the cloud API, which only reaches the thermostat
    while the gateway relays it. The schedule of a thermostat can only be
    changed while it is connected to the gateway.
    """

    local_modes = LOCAL_MODES
    measured_fields = frozenset({"Temperature"})
    volatile_fields = (
        VOLATILE_FIELDS
        | measured_fields
        | {
            "LoadMeasuredWatt",
            "SetPointTemp",
        }
    )

    async def set_energy_price(self, resource: Thermostat, price: float) -> bool:
        """Set the price per kWh through the cloud API, and remember it.

        Without remembering it, the next report of the thermostat would show
        the old price until the cloud is asked again.

        Args:
        ----
            resource: The Thermostat model.
            price: The price per kWh, see WG4API.set_energy_price().

        Returns:
        -------
            True if it succeeded.

        Raises:
        ------
            OJMicrolineError: The cloud refused the price.

        """
        done = await self._cloud(lambda: self.cloud.set_energy_price(resource, price))
        # A thermostat that is not in the snapshot has nothing to remember.
        data = self._snapshot.get(resource.serial_number)
        charge = round(price, 2)
        if data is not None and data.get("KwhCharge") != charge:
            data["KwhCharge"] = charge
            # The stored snapshot would bring the old price back otherwise.
            self._store_snapshot()
        return done

    def can_set_schedule(self, serial: str) -> bool:
        """Tell whether set_schedule() can change a schedule right now."""
        return serial in self._gateway.connected

    async def set_schedule(
        self,
        resource: Thermostat,
        days: Iterable[int],
        events: Sequence[tuple[time, int]],
    ) -> None:
        """Change the events of some days of a thermostat's schedule.

        The thermostat has to be connected to the gateway. It gets the
        schedule straight from the gateway, also while it is relayed; the
        upload that confirms it reaches OJ's gateway then as well. That
        confirmation takes up to a minute and a half, during which the
        thermostat connects anew. See WG4Gateway.set_schedule().

        A day has one to six events, on quarters of an hour and in order
        within the day. The temperature of the last event goes on until the
        first event of the next day.

        Args:
        ----
            resource: The Thermostat model.
            days: The days to change, 0 for Monday to 6 for Sunday.
            events: The events each of those days gets: the time of the event
                and its temperature in 1/100 °C.

        Raises:
        ------
            OJMicrolineScheduleError: The days or events do not fit the rules
                of the thermostat, or a temperature is outside its limits.
            OJMicrolineUnconfirmedError: The thermostat accepted the schedule,
                but did not confirm that it kept it. It may have.
            OJMicrolineError: The thermostat is not connected to the gateway,
                or did not take the schedule over.

        """
        try:
            chosen = check_days(days)
            day_events = [
                (_seconds(moment), temperature) for moment, temperature in events
            ]
            check_events(day_events, resource.min_temperature, resource.max_temperature)
        except ValueError as error:
            raise OJMicrolineScheduleError(str(error)) from error
        if not self.can_set_schedule(resource.serial_number):
            msg = (
                "The schedule can only be changed while the thermostat is "
                "connected to the gateway."
            )
            raise OJMicrolineError(msg)
        await self._gateway.set_schedule(resource.serial_number, chosen, day_events)

    def _known_locally(self, serial: str) -> bool:
        # A thermostat with a profile was relayed, and can be answered.
        return super()._known_locally(serial) or serial in self._gateway.profiles

    def _parse(self, data: dict[str, Any]) -> Thermostat:
        return Thermostat.from_wg4_json(data)

    def _load(self, serial: str) -> int | None:
        # The cloud API tells the load, measured or set at installation.
        known = {**self._snapshot.get(serial, {}), **self._live.get(serial, {})}
        return parse_wg4_load(known)

    async def _set_on_gateway(
        self,
        resource: Thermostat,
        regulation_mode: int,
        temperature: int | None,
        duration: int,  # noqa: ARG002 - the gateway cannot set a timed hold
    ) -> None:
        await self._gateway.set_mode(
            resource.serial_number, regulation_mode, temperature
        )


def _seconds(moment: time) -> int:
    """Return a time of the day in seconds since midnight.

    Raises
    ------
        ValueError: The time is not on a quarter of an hour.

    """
    if moment.microsecond:
        msg = f"{moment:%H:%M}: times must be on a quarter of an hour."
        raise ValueError(msg)
    return moment.hour * 3600 + moment.minute * 60 + moment.second
