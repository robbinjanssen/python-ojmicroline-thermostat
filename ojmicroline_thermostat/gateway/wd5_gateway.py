"""The gateway that WD5-series thermostats connect to.

A WD5 thermostat needs nothing that has to be recorded first: the gateway
answers its login with the time and asks for the full upload. A command is a
complete state, as OJ's gateway sends it. The thermostat itself only reports
the part of its state that matters in its mode, so the gateway keeps the last
command the thermostat accepted, with what the thermostat reported since laid
over it, as the base of the next one.

Adapted from patricknitsch/ioBroker.schlueter-thermostat (MIT), see the
LICENSE file in this package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

from ojmicroline_thermostat.exceptions import OJMicrolineConnectionError

from .protobuf import Message, encode_field, encode_frame
from .server import (
    WD5_R1099,
    DeviceGateway,
    GatewayEndpoint,
    GatewayMode,
    _Command,
    _Link,
)
from .wd5 import (
    FIELD_DEVICE_INFO,
    FIELD_SEQUENCE,
    FIELD_STATE,
    NOT_SET,
    Vacation,
    answer,
    changed_state,
    command,
    completed_state,
    decode_login,
    decode_state,
    decode_telemetry,
    default_state,
    describe,
    is_answer,
    login_answer,
    merged_state,
    upload_request,
    wants_answer,
)

if TYPE_CHECKING:
    import asyncio
    import ssl
    from collections.abc import Mapping
    from datetime import datetime


@dataclass(eq=False)
class _WD5Link(_Link):
    """The connection of a WD5 thermostat."""

    # Whether the thermostat uploaded what it holds.
    ready: bool = False
    # The state of the last command the thermostat accepted, of OJ's gateway
    # or this one, with what the thermostat reported since laid over it.
    template: bytes | None = None
    # Everything the thermostat reported so far.
    values: dict[str, Any] = field(default_factory=dict)


def _day(value: Any) -> date | None:
    """Read a day the way the cloud API and decode_state() write it."""
    if not isinstance(value, str) or value == NOT_SET:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _base_state(known: Mapping[str, Any]) -> bytes:
    """Build the state a first command starts from, out of what is known."""
    state = default_state(known)
    enabled = known.get("VacationEnabled")
    if enabled is None:
        return state
    # Without this a command would erase a vacation the thermostat has stored.
    vacation = Vacation(
        bool(enabled),
        _day(known.get("VacationBeginDay")),
        _day(known.get("VacationEndDay")),
    )
    return changed_state(state, vacation=vacation)


class WD5Gateway(DeviceGateway[_WD5Link]):
    """Accepts the connections of WD5-series thermostats on the local network.

    Only thermostats in allowed_serials are served; any other connection is
    closed without an answer. A thermostat can be answered locally right away:
    unlike the WG4 series, nothing has to be recorded first.

    Set on_state to be told what the thermostats report, and on_upstream to
    follow whether OJ's gateway can be used.

    The gateway keeps a history of what happened on its connections, which is
    also logged at debug level. It names serial numbers and addresses on the
    local network.
    """

    def __init__(
        self,
        ssl_context: ssl.SSLContext,
        *,
        mode: GatewayMode,
        endpoint: GatewayEndpoint = WD5_R1099,
        listen_host: str = "0.0.0.0",  # noqa: S104 - thermostats connect over the LAN
        listen_port: int | None = None,
    ) -> None:
        """Create a gateway; it listens once start() is called.

        Args:
        ----
            ssl_context: The TLS settings, see server_ssl_context().
            mode: Whether to relay the thermostats to OJ's gateway.
            endpoint: The device gateway of OJ that this gateway stands in
                for. It differs per brand.
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

    async def set_mode(
        self,
        serial: str,
        mode: int,
        temperature: int | None = None,
        *,
        end: datetime | None = None,
        known: Mapping[str, Any] | None = None,
    ) -> None:
        """Put a thermostat in a regulation mode and wait until it confirms.

        The command is a complete state. It starts from the last command the
        thermostat was sent on this connection, by OJ's gateway or this one,
        with what the thermostat reported since. What that leaves out, and
        everything before any command, comes from what is known of the
        thermostat, so pass what the cloud API knows: a setpoint that is not
        known would be replaced by a default in the thermostat.

        A command is never kept for later. It goes straight to the thermostat,
        also while it is relayed. If the thermostat confirms after this gave
        up waiting, on_state still reports the new state.

        Args:
        ----
            serial: The serial number of the thermostat.
            mode: The regulation mode to switch to.
            temperature: The setpoint in 1/100 °C of the comfort or the manual
                mode; None keeps the setpoint the thermostat has for it.
            end: When a comfort hold or a boost ends, in the local time of the
                thermostat, see wd5.local_in().
            known: The thermostat JSON of the cloud API, for what the
                thermostat did not report itself.

        Raises:
        ------
            OJMicrolineConnectionError: The thermostat is not connected, has
                not reported its state yet, or disconnected before answering.
            OJMicrolineTimeoutError: The thermostat did not answer in time.
            OJMicrolineError: The thermostat refused the command.

        """

        def prepare(link: _WD5Link) -> _Command:
            if not link.ready:
                msg = "The thermostat has not reported its state yet."
                raise OJMicrolineConnectionError(msg)
            template = link.template
            for underway in self._underway(link):
                # A change of the vendor's app reaches the thermostat first.
                template = underway.data(FIELD_STATE) or template
            # Everything that is known makes the state complete; a command of
            # OJ's gateway may leave parts out.
            base = _base_state({**(known or {}), **link.values})
            if template is not None:
                base = completed_state(template, base)
            state = changed_state(base, mode, temperature, end=end)

            def accept() -> None:
                # The thermostat took the state over, so the next command
                # starts from it.
                link.template = state
                self._report(link, Message.parse(encode_field(FIELD_STATE, state)))

            return (lambda sequence: command(sequence, state)), accept

        await self._command(serial, prepare)

    # --- What a WD5 thermostat says and expects -------------------------------

    def _new_link(self, serial: str, writer: asyncio.StreamWriter) -> _WD5Link:
        return _WD5Link(serial, writer)

    def _login_serial(self, message: Message) -> str | None:
        login = decode_login(message)
        return None if login is None else login.serial

    def _describe(self, message: Message) -> str:
        return describe(message)

    def _is_answer(self, message: Message) -> bool:
        return is_answer(message)

    def _expects_answer(self, message: Message) -> bool:
        return wants_answer(message)

    def _can_answer(self, serial: str) -> bool:  # noqa: ARG002 - every thermostat
        return True

    def _greeting(self, login: Message) -> bytes:
        sequence = login.varint(FIELD_SEQUENCE) or 0
        return encode_frame(login_answer(sequence)) + encode_frame(upload_request())

    def _reply(self, link: _WD5Link, message: Message) -> bytes | None:  # noqa: ARG002
        if not wants_answer(message):
            return None
        return encode_frame(answer(message.varint(FIELD_SEQUENCE) or 0))

    def _observe(self, link: _WD5Link, message: Message) -> None:
        # Decoding comes first: it refuses a malformed state, which must not
        # end up in the state that the next command starts from.
        self._report(link, message)
        state = message.data(FIELD_STATE)
        if state is not None and link.template is not None:
            # What was changed at the thermostat itself must survive the next
            # command, which the thermostat takes over as a whole.
            link.template = merged_state(link.template, state)
        if FIELD_STATE in message or FIELD_DEVICE_INFO in message:
            # From its upload on, the thermostat takes commands.
            link.ready = True

    def _observe_upstream(
        self,
        link: _WD5Link,  # noqa: ARG002
        frame: bytes,  # noqa: ARG002
        message: Message,
    ) -> None:
        # Decoding now tells a message that is not understood right away, and
        # keeps it from being learnt from; what it changes counts once the
        # thermostat accepted it.
        decode_telemetry(message)
        decode_state(message)

    def _delivered(self, link: _WD5Link, message: Message) -> None:
        self._report(link, message)
        state = message.data(FIELD_STATE)
        if state is not None:
            # A change made in the vendor's app, which the thermostat took
            # over as a whole.
            link.template = state

    def _report(self, link: _WD5Link, message: Message) -> None:
        """Keep what a message says about the thermostat, and pass it on."""
        fields = decode_telemetry(message) | decode_state(message)
        if fields:
            link.values.update(fields)
            self._notify(self.on_state, link.serial, fields)


__all__ = ["WD5Gateway"]
