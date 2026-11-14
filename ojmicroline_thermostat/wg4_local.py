"""Implementation of OJMicrolineAPI for WG4-series thermostats on a local gateway."""

from __future__ import annotations

from typing import Any

from .const import REGULATION_MANUAL, REGULATION_SCHEDULE
from .gateway import WG4Gateway
from .local import LocalAPI
from .models import Thermostat
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

    def _known_locally(self, serial: str) -> bool:
        # A thermostat with a profile was relayed, and can be answered.
        return super()._known_locally(serial) or serial in self._gateway.profiles

    def _parse(self, data: dict[str, Any]) -> Thermostat:
        return Thermostat.from_wg4_json(data)

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
