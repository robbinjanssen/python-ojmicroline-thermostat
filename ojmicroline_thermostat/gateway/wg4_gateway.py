"""The gateway that WG4-series thermostats connect to.

To answer a WG4 thermostat itself, the gateway needs the two answers OJ's
gateway gives it after a login. It records them as the thermostat's profile
while it relays the thermostat, and repeats them afterwards.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ojmicroline_thermostat.exceptions import (
    OJMicrolineConnectionError,
    OJMicrolineError,
    OJMicrolineUnconfirmedError,
)

from .profile import DeviceProfile
from .protobuf import Message, encode_field, encode_frame
from .server import (
    WG4_NORTH_AMERICA,
    DeviceGateway,
    GatewayEndpoint,
    GatewayMode,
    _Command,
    _Link,
)
from .wg4 import (
    FIELD_ACKNOWLEDGED,
    FIELD_CONFIGURATION,
    FIELD_DEVICE_INFO,
    FIELD_KIND,
    FIELD_SCHEDULE,
    FIELD_SEQUENCE,
    FIELD_STATE,
    KIND_SCHEDULE_UPLOAD,
    KIND_STATE_UPLOAD,
    acknowledgement,
    changed_schedule,
    changed_state,
    check_days,
    check_events,
    command,
    decode_schedule,
    decode_schedule_fields,
    decode_state,
    decode_telemetry,
    describe,
    login_acknowledgement,
    login_serial,
    response_field,
    schedule_command,
)

if TYPE_CHECKING:
    import ssl
    from collections.abc import Iterable, Mapping, Sequence

# Called with a serial number and the profile recorded for its thermostat.
ProfileListener = Callable[[str, DeviceProfile], None]

# How long a thermostat gets to upload its schedule again after it was made
# to connect anew. Reported: about a minute, and never more than 90 seconds.
READBACK_TIMEOUT = 90.0


@dataclass(eq=False)
class _WG4Link(_Link):
    """The connection of a WG4 thermostat."""

    # The state and schedule the thermostat holds, as it sent them last.
    state: bytes | None = None
    schedule: bytes | None = None
    # The answers OJ's gateway gave on this connection, as frames.
    configuration: bytes | None = None
    support: bytes | None = None


def _decoded(message: Message) -> dict[str, Any]:
    """Read what a message tells about a thermostat, in cloud API fields.

    A schedule that is not understood is left out, see
    decode_schedule_fields().

    Raises
    ------
        ValueError: The message is malformed.

    """
    if FIELD_STATE in message or FIELD_DEVICE_INFO in message:
        fields = decode_state(message)
    else:
        fields = decode_telemetry(message)
    return {**fields, **decode_schedule_fields(message)}


def _held(
    message: Message, state: bytes | None, schedule: bytes | None
) -> tuple[bytes | None, bytes | None]:
    """Return the state and schedule a thermostat holds after a message."""
    new_state = message.data(FIELD_STATE)
    new_schedule = message.data(FIELD_SCHEDULE)
    return (
        state if new_state is None else new_state,
        schedule if new_schedule is None else new_schedule,
    )


def _active(schedule: bytes, days: Iterable[int]) -> list[list[tuple[str, int]]]:
    """Return the active events of some days, 0 for Monday to 6 for Sunday.

    That is what the thermostat acts on. What a place that is switched off
    holds is left out: one thermostat was seen to keep it, which says nothing
    about others.

    Raises
    ------
        ValueError: The schedule is not understood.

    """
    numbers = {day + 1 for day in days}
    return [
        [
            (event["Clock"], event["TempFloor"])
            for event in day["Events"]
            if event["Active"]
        ]
        for day in decode_schedule(schedule)
        if day["WeekDayGrpNo"] in numbers
    ]


class WG4Gateway(DeviceGateway[_WG4Link]):
    """Accepts the connections of WG4-series thermostats on the local network.

    Only thermostats in allowed_serials are served; any other connection is
    closed without an answer. To be answered locally a thermostat also needs a
    profile, which the gateway records while it relays the thermostat.

    Set on_state to be told what the thermostats report, on_profile to store a
    recorded profile, and on_upstream to follow whether OJ's gateway can be
    used.

    The gateway keeps a history of what happened on its connections, which is
    also logged at debug level. It names serial numbers and addresses on the
    local network, and never holds a login or the content of a profile.
    """

    # Each argument is a separate setting of the gateway.
    def __init__(  # noqa: PLR0913 # pylint: disable=too-many-arguments
        self,
        ssl_context: ssl.SSLContext,
        *,
        mode: GatewayMode,
        endpoint: GatewayEndpoint = WG4_NORTH_AMERICA,
        profiles: Mapping[str, DeviceProfile] | None = None,
        listen_host: str = "0.0.0.0",  # noqa: S104 - thermostats connect over the LAN
        listen_port: int | None = None,
    ) -> None:
        """Create a gateway; it listens once start() is called.

        Args:
        ----
            ssl_context: The TLS settings, see server_ssl_context().
            mode: Whether to relay the thermostats to OJ's gateway.
            endpoint: The device gateway of OJ that this gateway stands in for.
            profiles: The profile of each thermostat, by serial number.
            listen_host: The address to listen on; every IPv4 address by
                default.
            listen_port: The port to listen on, if the thermostats reach this
                machine on another port than the endpoint's.

        """
        super().__init__(
            ssl_context,
            mode=mode,
            endpoint=endpoint,
            listen_host=listen_host,
            listen_port=listen_port,
        )
        self.profiles: dict[str, DeviceProfile] = dict(profiles or {})
        self.on_profile: ProfileListener | None = None
        # Who waits for a thermostat to upload its schedule, by serial number:
        # the connection it accepted the schedule on, which a new one has to
        # replace first, and the future that gets the schedule. A thermostat
        # takes no command while it reconnects, so it has one at most.
        self._readbacks: dict[str, tuple[_WG4Link, asyncio.Future[bytes]]] = {}

    async def set_mode(
        self, serial: str, mode: int, temperature: int | None = None
    ) -> None:
        """Put a thermostat in a regulation mode and wait until it confirms.

        The command is built from the state the thermostat holds: what it
        reported or accepted last, with what OJ's gateway sent it since. So
        it only works for a connected thermostat that has uploaded its state.
        A command is never kept for later: a setpoint that is applied hours
        late does more harm than an error.

        The command goes straight to the thermostat, also while it is relayed.
        If the thermostat confirms after this gave up waiting, on_state still
        reports the new state.

        Args:
        ----
            serial: The serial number of the thermostat.
            mode: The regulation mode to switch to.
            temperature: The setpoint in 1/100 °C of a temporary (comfort) or
                permanent (manual) hold; None keeps the setpoint the
                thermostat has stored for that hold.

        Raises:
        ------
            OJMicrolineConnectionError: The thermostat is not connected, has
                not reported its state yet, or disconnected before answering.
            OJMicrolineTimeoutError: The thermostat did not answer in time.
            OJMicrolineError: The thermostat refused the command.

        """

        def prepare(link: _WG4Link) -> _Command:
            held, schedule = link.state, link.schedule
            for underway in self._underway(link):
                # A change of the vendor's app reaches the thermostat first.
                held, schedule = _held(underway, held, schedule)
            if held is None:
                msg = "The thermostat has not reported its state yet."
                raise OJMicrolineConnectionError(msg)
            state = changed_state(held, mode, temperature)

            def accept() -> None:
                # The thermostat took the state over, so the next command
                # starts from it.
                link.state = state
                reported = Message.parse(encode_field(FIELD_STATE, state))
                self._notify(self.on_state, serial, decode_state(reported))

            return (lambda sequence: command(sequence, state, schedule)), accept

        await self._command(serial, prepare)

    async def set_schedule(
        self, serial: str, days: Iterable[int], events: Sequence[tuple[int, int]]
    ) -> None:
        """Change the events of some days of a thermostat's schedule.

        The command is the schedule the thermostat holds with only those days
        changed, see changed_schedule(), and the state it holds with a new
        time: mode and setpoints stay as they are. Nothing is sent when the
        schedule would not change.

        The acknowledgement of the thermostat does not tell that it kept the
        schedule, and it does not upload the schedule again by itself. So the
        gateway closes the connection once the thermostat accepted the
        command, and compares the days with what the thermostat uploads after
        it connected anew. The thermostat is offline for that moment.

        Args:
        ----
            serial: The serial number of the thermostat.
            days: The days to change, 0 for Monday to 6 for Sunday.
            events: The events each of those days gets: its time in seconds
                since midnight and its temperature in 1/100 °C, see
                check_events().

        Raises:
        ------
            ValueError: The days or events are not valid.
            OJMicrolineConnectionError: The thermostat is not connected, has
                not reported its schedule and state yet, or disconnected
                before answering.
            OJMicrolineTimeoutError: The thermostat did not answer in time.
            OJMicrolineUnconfirmedError: The thermostat accepted the command,
                but did not upload its schedule again in time.
            OJMicrolineError: The thermostat refused the command, its schedule
                is not understood, or it uploaded another schedule than the
                one it was sent.

        """
        chosen = check_days(days)
        check_events(events)
        readback: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
        sent: list[bytes] = []
        # Whether the caller still waits for the acknowledgement.
        waiting = [True]

        def prepare(link: _WG4Link) -> _Command | None:
            held, schedule = link.state, link.schedule
            for underway in self._underway(link):
                # A change of the vendor's app reaches the thermostat first.
                held, schedule = _held(underway, held, schedule)
            if held is None or schedule is None:
                msg = "The thermostat has not reported its schedule yet."
                raise OJMicrolineConnectionError(msg)
            try:
                changed = changed_schedule(schedule, chosen, events)
            except ValueError as error:
                msg = "The schedule of the thermostat is not understood."
                raise OJMicrolineError(msg) from error
            if changed == schedule:
                return None
            sent.append(changed)

            def accept() -> None:
                # The next command starts from it, or it would undo this one.
                link.schedule = changed
                self._notify(
                    self.on_state, serial, {"Schedules": decode_schedule(changed)}
                )
                if not waiting[0]:
                    # Nobody waits for the confirmation, and another command
                    # may be on this connection by now.
                    return
                # Closed while the command still holds the connection, so no
                # other command is sent on it; the thermostat connects again
                # by itself, and uploads what it kept.
                self._readbacks[serial] = (link, readback)
                self._event(
                    "Reconnecting thermostat %s to read back its schedule", serial
                )
                self._close(link)

            return (lambda sequence: schedule_command(sequence, held, changed)), accept

        try:
            if not await self._command(serial, prepare):
                self._event("The schedule of thermostat %s does not change", serial)
                return
        finally:
            waiting[0] = False
        stored = await self._read_back(serial, readback)
        if _active(stored, chosen) != _active(sent[0], chosen):
            self._event(
                "Thermostat %s uploaded another schedule than it accepted",
                serial,
                level=logging.WARNING,
            )
            msg = "The thermostat did not keep the schedule it was sent."
            raise OJMicrolineError(msg)
        self._event("Thermostat %s kept the new schedule", serial)

    async def stop(self) -> None:
        """Stop listening, close every connection, and end every read-back."""
        readbacks, self._readbacks = self._readbacks, {}
        for _, waiter in readbacks.values():
            if not waiter.done():
                msg = "The gateway stopped before the thermostat confirmed."
                waiter.set_exception(OJMicrolineConnectionError(msg))
        await super().stop()

    async def _read_back(self, serial: str, readback: asyncio.Future[bytes]) -> bytes:
        """Wait for the schedule a thermostat uploads after it connected anew.

        Args:
        ----
            serial: The serial number of the thermostat.
            readback: Gets the schedule, see _observe().

        Returns:
        -------
            The schedule.

        Raises:
        ------
            OJMicrolineUnconfirmedError: The schedule did not come in time.
            OJMicrolineConnectionError: The gateway stopped first.
            OJMicrolineError: The schedule is not understood.

        """
        try:
            async with asyncio.timeout(READBACK_TIMEOUT):
                stored = await readback
        except TimeoutError as error:
            self._event(
                "Thermostat %s did not upload its schedule within %g seconds",
                serial,
                READBACK_TIMEOUT,
                level=logging.WARNING,
            )
            msg = (
                "The thermostat accepted the schedule, but did not confirm that "
                "it kept it."
            )
            raise OJMicrolineUnconfirmedError(msg) from error
        finally:
            # A thermostat has one read-back at most; stop() may have taken
            # it away already.
            self._readbacks.pop(serial, None)
        try:
            decode_schedule(stored)
        except ValueError as error:
            msg = "The schedule of the thermostat is not understood."
            raise OJMicrolineError(msg) from error
        return stored

    # --- What a WG4 thermostat says and expects -------------------------------

    def _new_link(self, serial: str, writer: asyncio.StreamWriter) -> _WG4Link:
        return _WG4Link(serial, writer)

    def _login_serial(self, message: Message) -> str | None:
        return login_serial(message)

    def _describe(self, message: Message) -> str:
        return describe(message)

    def _is_answer(self, message: Message) -> bool:
        return FIELD_ACKNOWLEDGED in message and FIELD_KIND not in message

    def _expects_answer(self, message: Message) -> bool:
        # A thermostat answers everything but an answer.
        return not self._is_answer(message)

    def _can_answer(self, serial: str) -> bool:
        return serial in self.profiles

    def _greeting(self, login: Message) -> bytes:
        sequence = login.varint(FIELD_SEQUENCE) or 0
        return encode_frame(login_acknowledgement(sequence))

    def _reply(self, link: _WG4Link, message: Message) -> bytes | None:
        sequence = message.varint(FIELD_SEQUENCE) or 0
        kind = message.varint(FIELD_KIND)
        # Only a thermostat with a profile is answered, see _can_answer().
        profile = self.profiles[link.serial]
        if kind == KIND_SCHEDULE_UPLOAD:
            return encode_frame(acknowledgement(sequence)) + profile.support
        if kind == KIND_STATE_UPLOAD:
            return encode_frame(acknowledgement(sequence)) + profile.configuration
        if sequence:
            # A heartbeat, a change, or anything else that wants an answer.
            return encode_frame(acknowledgement(sequence))
        return None

    def _observe(self, link: _WG4Link, message: Message) -> None:
        self._take_over(link, message)
        schedule = message.data(FIELD_SCHEDULE)
        if schedule is None:
            return
        # A schedule the thermostat uploads on a new connection is the one it
        # kept.
        replaced, waiter = self._readbacks.get(link.serial, (link, None))
        if replaced is not link and waiter is not None and not waiter.done():
            waiter.set_result(schedule)

    def _observe_upstream(self, link: _WG4Link, frame: bytes, message: Message) -> None:
        self._record(link, frame, message)
        # Decoding now tells a message that is not understood right away, and
        # keeps it from being learnt from; what it changes counts once the
        # thermostat accepted it.
        _decoded(message)

    def _delivered(self, link: _WG4Link, message: Message) -> None:
        # A change made in the vendor's app arrives as a command.
        self._take_over(link, message)

    def _take_over(self, link: _WG4Link, message: Message) -> None:
        """Keep the state and schedule a message tells, and report them."""
        # Decoding comes first: it refuses a malformed state, which must not
        # become the state that the next command starts from.
        fields = _decoded(message)
        link.state, link.schedule = _held(message, link.state, link.schedule)
        if fields:
            self._notify(self.on_state, link.serial, fields)

    def _record(self, link: _WG4Link, frame: bytes, message: Message) -> None:
        """Keep the answers of OJ's gateway that make up a profile."""
        number = response_field(message)
        if number is None:
            return
        if number == FIELD_CONFIGURATION:
            link.configuration = frame
        else:
            link.support = frame
        if link.configuration is None or link.support is None:
            return
        profile = DeviceProfile(link.configuration, link.support)
        if self.profiles.get(link.serial) == profile:
            return
        self.profiles[link.serial] = profile
        self._event("Recorded the profile of thermostat %s", link.serial)
        self._notify(self.on_profile, link.serial, profile)


__all__ = ["ProfileListener", "WG4Gateway"]
