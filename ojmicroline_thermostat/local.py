"""What the APIs for thermostats on a local gateway have in common."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from .const import REGULATION_MANUAL
from .exceptions import (
    OJMicrolineError,
    OJMicrolineTimeoutError,
    OJMicrolineUnauthorizedError,
)
from .gateway import DeviceGateway, GatewayMode

if TYPE_CHECKING:
    from collections.abc import Awaitable, Mapping

    from .models import Thermostat
    from .ojmicroline import RequestFunc
    from .wd5 import WD5API
    from .wg4 import WG4API

_LOGGER = logging.getLogger(__name__)

# How long the cloud API gets to answer when the thermostats are fetched. The
# thermostats on the gateway do not depend on that answer.
CLOUD_TIMEOUT = 10.0

# The JSON of each thermostat as the cloud API returns it, by serial number.
Snapshot = dict[str, dict[str, Any]]
# Called with a new snapshot, for the caller to store.
SnapshotListener = Callable[[Snapshot], None]

_T = TypeVar("_T")
_CloudT = TypeVar("_CloudT", bound="WD5API | WG4API")
_GatewayT = TypeVar("_GatewayT", bound=DeviceGateway)


class LocalAPI(ABC, Generic[_CloudT, _GatewayT]):
    """Controls thermostats through a local gateway.

    A thermostat that connects to the gateway reports to this API directly and
    takes its commands from it. The cloud API still supplies what a thermostat
    does not report itself, such as its temperature limits, and it serves the
    thermostats of the account that never connected to the gateway.

    The last answer of the cloud API is kept as a snapshot, so the thermostats
    on the gateway keep working when the cloud cannot be reached. Pass the
    stored snapshot in, and store the one on_snapshot is called with. A
    snapshot may hold the email address of the account.

    The API takes over the on_state listener and the allowed serial numbers of
    the gateway. Starting and stopping the gateway is up to the caller.
    """

    supports_notifications: bool = True
    # The regulation modes the gateway can set by itself.
    local_modes: tuple[int, ...] = ()

    def __init__(
        self,
        cloud: _CloudT,
        gateway: _GatewayT,
        *,
        snapshot: Mapping[str, Mapping[str, Any]] | None = None,
        on_snapshot: SnapshotListener | None = None,
    ) -> None:
        """Create a new instance of the API object.

        Args:
        ----
            cloud: The cloud API of the account the thermostats belong to.
            gateway: The gateway the thermostats connect to.
            snapshot: The snapshot that was stored last, if any.
            on_snapshot: Called with every new snapshot, to store it.

        """
        self.cloud = cloud
        self.host = cloud.host
        # Why the cloud API could not be used the last time the thermostats
        # were fetched; None if it answered.
        self.cloud_error: OJMicrolineError | None = None
        self._gateway = gateway
        self._on_snapshot = on_snapshot
        self._snapshot: Snapshot = {
            serial: dict(data) for serial, data in (snapshot or {}).items()
        }
        # What each thermostat on the gateway reported, in cloud API fields.
        self._live: dict[str, dict[str, Any]] = {}
        self._changed: set[str] = set()
        self._wake = asyncio.Event()
        self._subscribed = False
        gateway.allowed_serials = set(self._snapshot)
        gateway.on_state = self._on_state

    @property
    def request(self) -> RequestFunc:
        """Return the function for HTTP requests, which the cloud API uses."""
        return self.cloud.request

    @request.setter
    def request(self, request: RequestFunc) -> None:
        self.cloud.request = request

    async def login(self) -> None:
        """Do nothing: a thermostat on the gateway needs no session.

        The cloud API logs in when it is used. That way a cloud that cannot be
        reached never delays a command to a thermostat on the gateway.
        """

    def invalidate_session(self) -> None:
        """Forget the session of the cloud API."""
        self.cloud.invalidate_session()

    async def get_thermostats(self) -> list[Thermostat]:
        """Get all the thermostats of the account, without energy usage.

        The cloud API is asked for the thermostats first. If it fails or takes
        too long, the last snapshot is used and cloud_error tells why.

        Returns
        -------
            A list of Thermostat objects. One that is connected to the gateway
            has the values it reported itself.

        Raises
        ------
            OJMicrolineError: The cloud API failed and there is no snapshot.

        """
        await self._refresh()
        return [self._thermostat(serial) for serial in self._snapshot]

    async def get_energy_usage(self, resource: Thermostat) -> list[float]:
        """Get the energy usage of a thermostat from the cloud API.

        Args:
        ----
            resource: The Thermostat model.

        Returns:
        -------
            The energy usage for the current day and the six previous days.

        Raises:
        ------
            OJMicrolineError: The cloud API failed.

        """
        return await self._cloud(lambda: self.cloud.get_energy_usage(resource))

    async def set_regulation_mode(
        self,
        resource: Thermostat,
        regulation_mode: int,
        temperature: int | None,
        duration: int,
    ) -> bool:
        """Set the regulation mode of a thermostat.

        For a thermostat on the gateway, the modes in local_modes go straight
        to the thermostat. Other modes go through the cloud API, which only
        reaches the thermostat while the gateway relays it. A thermostat that
        never connected to the gateway is set through the cloud API.

        Args:
        ----
            resource: The Thermostat model.
            regulation_mode: The mode to set the thermostat to.
            temperature: The temperature to set or None.
            duration: The duration in minutes (comfort mode only).

        Returns:
        -------
            True if it succeeded.

        Raises:
        ------
            OJMicrolineError: The thermostat or the cloud API did not take the
                mode over.

        """
        serial = resource.serial_number
        if self.on_gateway(serial):
            if regulation_mode in self.local_modes:
                if (
                    regulation_mode == REGULATION_MANUAL
                    and temperature is not None
                    and not resource.min_temperature
                    <= temperature
                    <= resource.max_temperature
                ):
                    msg = "The temperature is outside the limits of the thermostat."
                    raise OJMicrolineError(msg)
                await self._set_on_gateway(
                    resource, regulation_mode, temperature, duration
                )
                return True
            if not self._gateway.is_relayed(serial):
                msg = (
                    "This mode is set through the cloud, which the thermostat is "
                    "not connected to."
                )
                raise OJMicrolineError(msg)
        return await self._cloud(
            lambda: self.cloud.set_regulation_mode(
                resource, regulation_mode, temperature, duration
            )
        )

    async def get_notifications(self) -> list[Thermostat]:
        """Wait until a thermostat on the gateway reports something.

        Returns
        -------
            Every thermostat on the first call, and afterwards the thermostats
            that reported since the last call.

        """
        if not self._subscribed:
            self._subscribed = True
            changed = set(self._snapshot)
        else:
            await self._wake.wait()
            changed = self._changed
        self._wake.clear()
        self._changed = set()
        return [
            self._thermostat(serial)
            for serial in sorted(changed)
            if serial in self._snapshot
        ]

    def on_gateway(self, serial: str) -> bool:
        """Tell whether a thermostat connects to the gateway, now or earlier."""
        return serial in self._live

    # --- What a series does differently ---------------------------------------

    @abstractmethod
    def _parse(self, data: dict[str, Any]) -> Thermostat:
        """Build a thermostat from the JSON of the cloud API."""

    @abstractmethod
    async def _set_on_gateway(
        self,
        resource: Thermostat,
        regulation_mode: int,
        temperature: int | None,
        duration: int,
    ) -> None:
        """Send one of the local modes to a thermostat on the gateway."""

    # --- What the series share --------------------------------------------------

    def _on_state(self, serial: str, fields: dict[str, Any]) -> None:
        """Keep what a thermostat on the gateway reports."""
        self._live.setdefault(serial, {}).update(fields)
        self._changed.add(serial)
        self._wake.set()

    def _thermostat(self, serial: str) -> Thermostat:
        """Build a thermostat from the snapshot and what it reported itself."""
        data = dict(self._snapshot[serial])
        on_gateway = self.on_gateway(serial)
        if on_gateway:
            # The cloud does not know whether the thermostat is connected here.
            data["Online"] = False
            data.update(self._live.get(serial, {}))
        thermostat = self._parse(data)
        if on_gateway and self._gateway.mode is GatewayMode.LOCAL:
            thermostat.supported_regulation_modes = list(self.local_modes)
        return thermostat

    async def _refresh(self) -> None:
        """Fetch a new snapshot from the cloud API, if it answers in time."""
        try:
            async with asyncio.timeout(CLOUD_TIMEOUT):
                snapshot = await self._cloud(self.cloud.get_thermostats_json)
        except TimeoutError as error:
            msg = "The cloud API did not answer in time."
            self.cloud_error = OJMicrolineTimeoutError(msg)
            if not self._snapshot:
                raise self.cloud_error from error
        except OJMicrolineError as error:
            self.cloud_error = error
            if not self._snapshot:
                raise
        else:
            self.cloud_error = None
            # An empty answer is more likely a hiccup of the cloud than an
            # account without thermostats; it must not lock them out.
            if snapshot and snapshot != self._snapshot:
                self._snapshot = snapshot
                self._gateway.allowed_serials = set(snapshot)
                if self._on_snapshot is not None:
                    self._on_snapshot(
                        {serial: dict(data) for serial, data in snapshot.items()}
                    )
        if self.cloud_error is not None:
            _LOGGER.debug(
                "Using the stored thermostats, the cloud API failed: %s",
                self.cloud_error,
            )

    async def _cloud(self, call: Callable[[], Awaitable[_T]]) -> _T:
        """Log in to the cloud API and call it, once more if the session expired."""
        await self.cloud.login()
        try:
            return await call()
        except OJMicrolineUnauthorizedError:
            self.cloud.invalidate_session()
            await self.cloud.login()
            return await call()
