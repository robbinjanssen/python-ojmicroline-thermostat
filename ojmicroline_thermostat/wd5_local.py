"""Implementation of OJMicrolineAPI for WD5-series thermostats on a local gateway."""

from __future__ import annotations

from typing import Any

from .const import (
    REGULATION_BOOST,
    REGULATION_COMFORT,
    REGULATION_ECO,
    REGULATION_MANUAL,
    REGULATION_SCHEDULE,
)
from .gateway import WD5Gateway
from .gateway.wd5 import local_in
from .local import LocalAPI
from .models import Thermostat
from .wd5 import WD5API

# The regulation modes the gateway can set by itself. The vacation is set
# through the cloud API, and so is frost protection, which is not confirmed
# on a thermostat.
LOCAL_MODES = (
    REGULATION_SCHEDULE,
    REGULATION_COMFORT,
    REGULATION_MANUAL,
    REGULATION_BOOST,
    REGULATION_ECO,
)
# A boost lasts an hour, as it does when the cloud API starts one.
BOOST_DURATION = 60


class WD5LocalAPI(LocalAPI[WD5API, WD5Gateway]):
    """Controls WD5-series thermostats through a local gateway.

    A thermostat that connects to the gateway reports to this API directly and
    takes its commands from it. The cloud API still supplies what a thermostat
    does not report itself, such as its temperature limits and its group, and
    it serves the thermostats of the account that never connected to the
    gateway.

    The last answer of the cloud API is kept as a snapshot, so the thermostats
    on the gateway keep working when the cloud cannot be reached. Pass the
    stored snapshot in, and store the one on_snapshot is called with.

    The modes in LOCAL_MODES go straight to a thermostat on the gateway, and
    to that thermostat only: the cloud API sets a mode for every thermostat in
    a group. Other modes go through the cloud API, which only reaches the
    thermostat while the gateway relays it.
    """

    local_modes = LOCAL_MODES

    def _parse(self, data: dict[str, Any]) -> Thermostat:
        return Thermostat.from_wd5_json(data)

    async def _set_on_gateway(
        self,
        resource: Thermostat,
        regulation_mode: int,
        temperature: int | None,
        duration: int,
    ) -> None:
        serial = resource.serial_number
        known = {**self._snapshot.get(serial, {}), **self._live.get(serial, {})}
        minutes = {REGULATION_COMFORT: duration, REGULATION_BOOST: BOOST_DURATION}.get(
            regulation_mode
        )
        end = None
        if minutes is not None:
            # The thermostat counts in its own time zone, which the cloud API
            # tells for every thermostat.
            end = local_in(minutes, known["TimeZone"])
        await self._gateway.set_mode(
            serial, regulation_mode, temperature, end=end, known=known
        )
