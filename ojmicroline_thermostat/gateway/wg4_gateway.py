"""The gateway that WG4-series thermostats connect to.

To answer a WG4 thermostat itself, the gateway needs the two answers OJ's
gateway gives it after a login. It records them as the thermostat's profile
while it relays the thermostat, and repeats them afterwards.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ojmicroline_thermostat.exceptions import OJMicrolineConnectionError

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
    changed_state,
    command,
    decode_state,
    decode_telemetry,
    describe,
    login_acknowledgement,
    login_serial,
    response_field,
)

if TYPE_CHECKING:
    import asyncio
    import ssl
    from collections.abc import Mapping

# Called with a serial number and the profile recorded for its thermostat.
ProfileListener = Callable[[str, DeviceProfile], None]


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

    Raises
    ------
        ValueError: The message is malformed.

    """
    if FIELD_STATE in message or FIELD_DEVICE_INFO in message:
        return decode_state(message)
    return decode_telemetry(message)


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
        # Decoding comes first: it refuses a malformed state, which must not
        # become the state that the next command starts from.
        fields = _decoded(message)
        link.state, link.schedule = _held(message, link.state, link.schedule)
        if fields:
            self._notify(self.on_state, link.serial, fields)

    def _observe_upstream(self, link: _WG4Link, frame: bytes, message: Message) -> None:
        self._record(link, frame, message)
        # Decoding now tells a message that is not understood right away, and
        # keeps it from being learnt from; what it changes counts once the
        # thermostat accepted it.
        _decoded(message)

    def _delivered(self, link: _WG4Link, message: Message) -> None:
        # A change made in the vendor's app arrives as a command.
        self._observe(link, message)

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
