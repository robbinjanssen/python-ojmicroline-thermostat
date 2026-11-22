"""What the APIs for thermostats on a local gateway have in common."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from http import HTTPStatus
from time import monotonic
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from .exceptions import (
    OJMicrolineConnectionError,
    OJMicrolineError,
    OJMicrolineHTTPError,
    OJMicrolineResultsError,
    OJMicrolineTimeoutError,
    OJMicrolineUnauthorizedError,
)
from .gateway import DeviceGateway, GatewayMode

if TYPE_CHECKING:
    from collections.abc import Awaitable, Iterable, Mapping

    from .models import Thermostat
    from .ojmicroline import RequestFunc
    from .wd5 import WD5API
    from .wg4 import WG4API

_LOGGER = logging.getLogger(__name__)

# How long the cloud API gets to answer when the thermostats are fetched, and
# when it sets a mode that the gateway can set as well. The thermostats on the
# gateway do not depend on that answer.
CLOUD_TIMEOUT = 10.0

# The JSON of each thermostat as the cloud API returns it, by serial number.
Snapshot = dict[str, dict[str, Any]]
# Called with a new snapshot, for the caller to store.
SnapshotListener = Callable[[Snapshot], None]

# Called with a serial number and the energy in kWh that the thermostat used
# since its previous report, for the caller to add up.
UsageListener = Callable[[str, float], None]

# How long a report counts for when the one before it is not known, or is too
# long ago to tell what the thermostat did in between. A thermostat reports
# once a minute.
REPORT_INTERVAL = 60.0
# The longest time between two reports that counts in full.
MAX_REPORT_INTERVAL = 120.0

# The fields of the thermostat JSON that change by themselves in every series.
# A snapshot that only differs in those is not worth storing again.
VOLATILE_FIELDS = frozenset({"Heating", "Online"})

_T = TypeVar("_T")
_CloudT = TypeVar("_CloudT", bound="WD5API | WG4API")
_GatewayT = TypeVar("_GatewayT", bound=DeviceGateway[Any])


# The HTTP statuses below 500 that tell of a cloud API that cannot be used
# right now, not of a request it turned down.
_NOT_NOW = frozenset({HTTPStatus.REQUEST_TIMEOUT, HTTPStatus.TOO_MANY_REQUESTS})


def cloud_unreachable(error: OJMicrolineError) -> bool:
    """Tell a cloud API that cannot be used from one that refused a request.

    Only ask this for an error of the cloud API. A thermostat on the gateway
    that does not answer raises the same connection and timeout errors, which
    say nothing about the cloud.
    """
    if isinstance(error, OJMicrolineHTTPError):
        # Out of order (5xx) or busy, not a request it turned down (4xx).
        return error.status >= HTTPStatus.INTERNAL_SERVER_ERROR or (
            error.status in _NOT_NOW
        )
    return isinstance(error, OJMicrolineConnectionError | OJMicrolineTimeoutError)


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
    # The fields of the thermostat JSON that change by themselves.
    volatile_fields: frozenset[str] = VOLATILE_FIELDS
    # The fields a thermostat measures. For a thermostat on the gateway they
    # only come from the thermostat: the cloud does not hear from it in local
    # mode, so what it answers is no measurement. The sensors of a tester
    # showed 0 until the first report of the thermostat.
    measured_fields: frozenset[str] = frozenset()
    # Whether a mode for a relayed thermostat goes through the cloud first.
    # The cloud of the WD5 series keeps the mode of a group and does not take
    # over what the thermostat reports, so a mode the gateway set alone would
    # not show in the vendor's app.
    cloud_first: bool = False

    def __init__(  # noqa: PLR0913 # pylint: disable=too-many-arguments
        self,
        cloud: _CloudT,
        gateway: _GatewayT,
        *,
        snapshot: Mapping[str, Mapping[str, Any]] | None = None,
        on_snapshot: SnapshotListener | None = None,
        known: Iterable[str] = (),
        on_usage: UsageListener | None = None,
    ) -> None:
        """Create a new instance of the API object.

        Args:
        ----
            cloud: The cloud API of the account the thermostats belong to.
            gateway: The gateway the thermostats connect to.
            snapshot: The snapshot that was stored last, if any.
            on_snapshot: Called with a new snapshot, to store it. It is not
                called for one that only differs in what changes by itself,
                such as a measured temperature.
            known: The serial numbers of the thermostats that connected to
                the gateway before, see the property known. Without them, a
                thermostat that only the gateway reaches is taken for one
                that the cloud reaches, until it connects again.
            on_usage: Called with a serial number and the energy in kWh that
                the thermostat used since its previous report, for every
                report that OJ's gateway does not get: in local mode, and in
                relay mode while OJ's gateway is unreachable. The cloud API
                does not count that energy, so add it to what
                get_energy_usage() returns. It is 0 for a thermostat that is
                not heating, and nothing is reported for a thermostat whose
                load is not known.

        """
        self.cloud = cloud
        self.host = cloud.host
        # Why the cloud API could not be used the last time the thermostats
        # were fetched; None if it answered.
        self.cloud_error: OJMicrolineError | None = None
        self._gateway = gateway
        self._on_snapshot = on_snapshot
        self._on_usage = on_usage
        # When each thermostat last told whether it is heating (monotonic).
        self._reported_at: dict[str, float] = {}
        self._snapshot: Snapshot = {
            serial: dict(data) for serial, data in (snapshot or {}).items()
        }
        # What each thermostat on the gateway reported, in cloud API fields.
        self._live: dict[str, dict[str, Any]] = {}
        self._known = set(known)
        self._changed: set[str] = set()
        self._wake = asyncio.Event()
        # The task that waits for notifications. Another task is a new
        # subscriber, which gets every thermostat first.
        self._subscriber: asyncio.Task[Any] | None = None
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
            has the values it reported itself. Its measured temperature is
            None until it reported one, about a minute after it connected.

        Raises
        ------
            OJMicrolineError: The cloud API failed and there is no snapshot.

        """
        await self._refresh()
        return [self._thermostat(serial) for serial in self._snapshot]

    async def refresh(self) -> None:
        """Fetch what the cloud API knows of the thermostats right now.

        Call this before changing something that the cloud replaces as a
        whole, such as the settings of a group: cloud_thermostat() is as
        recent as the last fetch.

        Raises
        ------
            OJMicrolineError: The cloud API failed.

        """
        await self._refresh()
        if self.cloud_error is not None:
            raise self.cloud_error

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
        *,
        through_cloud: Callable[[], Awaitable[object]] | None = None,
    ) -> bool:
        """Set the regulation mode of a thermostat.

        For a thermostat on the gateway, the modes in local_modes go straight
        to the thermostat, see sets_on_gateway(). Other modes go through the
        cloud API, which only reaches the thermostat while the gateway relays
        it. A thermostat that is not on the gateway is set through the cloud
        API; see can_set().

        When cloud_first sends a local mode through the cloud API and that API
        cannot be reached, the gateway sets the mode itself. The cloud gets
        CLOUD_TIMEOUT seconds then. A request the cloud API refuses (HTTP 4xx)
        is an error: the gateway does not overrule it.

        Args:
        ----
            resource: The Thermostat model.
            regulation_mode: The mode to set the thermostat to.
            temperature: The temperature to set or None.
            duration: The duration in minutes (comfort mode only).
            through_cloud: Sets the mode in the cloud in the caller's own way,
                where this API decides to go through the cloud; the cloud
                API's set_regulation_mode() when omitted. A caller that sets
                more than the mode with it, such as a whole group, passes it
                so the choice between cloud and gateway stays here.

        Returns:
        -------
            True if it succeeded.

        Raises:
        ------
            OJMicrolineError: The thermostat or the cloud API did not take the
                mode over.

        """
        serial = resource.serial_number
        if self.sets_on_gateway(serial, regulation_mode):
            await self.set_on_gateway(resource, regulation_mode, temperature, duration)
            return True
        if not self.cloud_reaches(serial):
            msg = (
                "This mode is set through the cloud, which the thermostat is "
                "not connected to."
            )
            raise OJMicrolineError(msg)

        async def in_the_cloud() -> bool:
            if through_cloud is not None:
                await through_cloud()
                return True
            return await self._cloud(
                lambda: self.cloud.set_regulation_mode(
                    resource, regulation_mode, temperature, duration
                )
            )

        if not (self.on_gateway(serial) and regulation_mode in self.local_modes):
            # Only the cloud can do this.
            return await in_the_cloud()
        reason = "did not answer in time"
        try:
            # The gateway can take over, so the cloud does not get long.
            async with asyncio.timeout(CLOUD_TIMEOUT):
                return await in_the_cloud()
        except TimeoutError:
            pass
        except OJMicrolineError as error:
            if not cloud_unreachable(error):
                raise
            reason = "cannot be reached"
        _LOGGER.debug(
            "The cloud API %s; the gateway sets thermostat %s", reason, serial
        )
        await self.set_on_gateway(resource, regulation_mode, temperature, duration)
        return True

    async def set_on_gateway(
        self,
        resource: Thermostat,
        regulation_mode: int,
        temperature: int | None,
        duration: int,
    ) -> None:
        """Send a mode to a thermostat on the gateway, not through the cloud.

        Use this when a change through the cloud failed because the cloud API
        cannot be reached. The vendor's app does not see a mode set this way
        on a thermostat of the WD5 series.

        Args:
        ----
            resource: The Thermostat model.
            regulation_mode: One of local_modes.
            temperature: The temperature to set or None.
            duration: The duration in minutes (comfort mode only).

        Raises:
        ------
            OJMicrolineError: The thermostat is not on the gateway, the mode is
                not one of local_modes, the temperature is outside the limits
                of the thermostat, or the thermostat did not take the mode over.

        """
        if not self.on_gateway(resource.serial_number):
            msg = "The thermostat is not connected to the gateway."
            raise OJMicrolineError(msg)
        if regulation_mode not in self.local_modes:
            msg = "The gateway cannot set this mode."
            raise OJMicrolineError(msg)
        # The cloud API checks a temperature; the thermostat does not.
        if temperature is not None and not (
            resource.min_temperature <= temperature <= resource.max_temperature
        ):
            msg = "The temperature is outside the limits of the thermostat."
            raise OJMicrolineError(msg)
        await self._set_on_gateway(resource, regulation_mode, temperature, duration)

    async def get_notifications(self) -> list[Thermostat]:
        """Wait until a thermostat on the gateway reports something.

        Only the thermostats on the gateway report here. A thermostat that is
        served by the cloud API changes through get_thermostats() only, so
        keep polling that.

        Returns
        -------
            Every thermostat on the first call of a task that waits for
            notifications, and afterwards the thermostats that reported since
            the last call.

        """
        task = asyncio.current_task()
        if task is not self._subscriber:
            self._subscriber = task
            changed = set(self._snapshot)
        else:
            await self._wake.wait()
            changed = set(self._changed)
        self._wake.clear()
        self._changed -= changed
        thermostats = []
        for serial in sorted(changed & set(self._snapshot)):
            try:
                thermostats.append(self._thermostat(serial))
            except (KeyError, TypeError, ValueError):
                # One thermostat that cannot be read must not hold up others.
                _LOGGER.warning(
                    "The data of thermostat %s cannot be read", serial, exc_info=True
                )
        return thermostats

    def on_gateway(self, serial: str) -> bool:
        """Tell whether a thermostat is served by the gateway.

        That is a thermostat that is connected to it now, and in local mode
        also one that was connected before: it does not reach the cloud.
        """
        if serial in self._gateway.connected:
            return True
        return self._gateway.mode is GatewayMode.LOCAL and self._known_locally(serial)

    @property
    def known(self) -> frozenset[str]:
        """Return the serial numbers of the thermostats the gateway served.

        Store them, and pass them in again after a restart: in local mode a
        thermostat that connected before does not reach the cloud, also while
        it has not connected again yet.
        """
        return frozenset(self._known)

    def cloud_reaches(self, serial: str) -> bool:
        """Tell whether a change through the cloud API reaches a thermostat.

        It does for a thermostat that is not on the gateway, and for one that
        the gateway relays to OJ's gateway.
        """
        return not self.on_gateway(serial) or self._gateway.is_relayed(serial)

    def cloud_thermostat(self, serial: str) -> Thermostat | None:
        """Return a thermostat as the cloud API described it last.

        That leaves out what the thermostat reported to the gateway, such as
        a mode that was set on it alone. A change of its group in the cloud
        starts from this.
        """
        data = self._snapshot.get(serial)
        return None if data is None else self._parse(dict(data))

    def can_set(self, serial: str, regulation_mode: int) -> bool:
        """Tell whether set_regulation_mode() can set a mode right now."""
        if self.on_gateway(serial) and regulation_mode in self.local_modes:
            return True
        return self.cloud_reaches(serial)

    def sets_on_gateway(self, serial: str, regulation_mode: int) -> bool:
        """Tell whether set_regulation_mode() sends a mode to the gateway.

        That is a mode of local_modes for a thermostat on the gateway, unless
        cloud_first sends it through the cloud: for a thermostat the gateway
        relays, which the cloud reaches, so the vendor's app shows the mode.
        """
        if not self.on_gateway(serial) or regulation_mode not in self.local_modes:
            return False
        return not (self.cloud_first and self._gateway.is_relayed(serial))

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

    @abstractmethod
    def _load(self, serial: str) -> int | None:
        """Return the load of a thermostat's heating in W, if it is known."""

    def _known_locally(self, serial: str) -> bool:
        """Tell whether a thermostat was served by the gateway before."""
        return serial in self._known

    # --- What the series share --------------------------------------------------

    def _on_state(self, serial: str, fields: dict[str, Any]) -> None:
        """Keep what a thermostat on the gateway reports."""
        self._known.add(serial)
        self._live.setdefault(serial, {}).update(fields)
        if "Heating" in fields:
            self._count(serial, heating=bool(fields["Heating"]))
        self._changed.add(serial)
        self._wake.set()

    def _count(self, serial: str, *, heating: bool) -> None:
        """Tell the caller what a thermostat used since its previous report.

        A thermostat switches its load fully on or off, and tells about once
        a minute whether it is on. OJ's cloud counts the energy usage from
        those reports: a whole number of minutes times the load, which a
        power meter confirmed on an OWD5. A report that OJ's gateway gets is
        counted there. One that it does not get is counted here.
        """
        now = monotonic()
        previous = self._reported_at.get(serial)
        self._reported_at[serial] = now
        if self._on_usage is None or self._gateway.is_relayed(serial):
            return
        load = self._load(serial)
        if not load:
            return
        seconds = REPORT_INTERVAL
        if previous is not None and now - previous <= MAX_REPORT_INTERVAL:
            seconds = now - previous
        self._on_usage(serial, load * seconds / 3_600_000 if heating else 0.0)

    def _thermostat(self, serial: str) -> Thermostat:
        """Build a thermostat from the snapshot and what it reported itself."""
        data = dict(self._snapshot[serial])
        on_gateway = self.on_gateway(serial)
        if on_gateway:
            # The cloud does not know whether the thermostat is connected here,
            # nor what it measures: that is not known until it tells.
            data["Online"] = False
            data.update(dict.fromkeys(self.measured_fields))
            data.update(self._live.get(serial, {}))
        thermostat = self._parse(data)
        if not self.cloud_reaches(serial):
            # Only the gateway reaches the thermostat now.
            thermostat.supported_regulation_modes = list(self.local_modes)
        return thermostat

    async def _refresh(self) -> None:
        """Fetch a new snapshot from the cloud API, if it answers in time."""
        try:
            async with asyncio.timeout(CLOUD_TIMEOUT):
                try:
                    snapshot = await self._cloud(self.cloud.get_thermostats_json)
                except OJMicrolineUnauthorizedError:
                    # The session expired; _cloud() logs in anew.
                    snapshot = await self._cloud(self.cloud.get_thermostats_json)
            self._check(snapshot)
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
                stored = self._lasting(self._snapshot)
                self._snapshot = snapshot
                self._gateway.allowed_serials = set(snapshot)
                # A measured temperature alone is not worth storing again.
                if self._lasting(snapshot) != stored:
                    self._store_snapshot()
        if self.cloud_error is not None:
            _LOGGER.debug(
                "Using the stored thermostats, the cloud API failed: %s",
                self.cloud_error,
            )

    def _check(self, snapshot: Snapshot) -> None:
        """Refuse a snapshot that holds a thermostat that cannot be read.

        It would replace the stored one, which works, for good.

        Raises
        ------
            OJMicrolineResultsError: A thermostat is not what the API is
                known to return.

        """
        try:
            for data in snapshot.values():
                self._parse(dict(data))
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            # The answer itself stays out of the error: it may hold the email
            # address of the account, and an error ends up in logs.
            msg = "The API returned a thermostat in a form that is not understood."
            raise OJMicrolineResultsError(msg) from error

    def _store_snapshot(self) -> None:
        """Hand the snapshot to the caller, to store it."""
        if self._on_snapshot is not None:
            self._on_snapshot(
                {serial: dict(data) for serial, data in self._snapshot.items()}
            )

    def _lasting(self, snapshot: Snapshot) -> Snapshot:
        """Return a snapshot without the fields that change by themselves."""
        return {
            serial: {
                name: value
                for name, value in data.items()
                if name not in self.volatile_fields
            }
            for serial, data in snapshot.items()
        }

    async def _cloud(self, call: Callable[[], Awaitable[_T]]) -> _T:
        """Log in to the cloud API and call it.

        login() of this API does nothing, so the cloud API logs in here. When
        its session expired, the session is forgotten: OJMicroline calls
        again, as it does for every API, and that call logs in anew.
        """
        await self.cloud.login()
        try:
            return await call()
        except OJMicrolineUnauthorizedError:
            self.cloud.invalidate_session()
            raise
