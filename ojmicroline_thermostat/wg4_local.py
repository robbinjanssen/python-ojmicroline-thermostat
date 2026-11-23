"""Implementation of OJMicrolineAPI for WG4-series thermostats on a local gateway."""

from __future__ import annotations

from typing import Any

from .const import REGULATION_MANUAL, REGULATION_SCHEDULE
from .gateway import WG4Gateway
from .local import VOLATILE_FIELDS, LocalAPI
from .models import Thermostat
from .models.thermostat import parse_wg4_load
from .wg4 import WG4API

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
    while the gateway relays it.
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
