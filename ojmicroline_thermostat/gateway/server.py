"""The listener that WG4-series thermostats connect to.

A thermostat that resolves the hostname of OJ's device gateway to this machine
connects here. It logs in with its serial number, uploads its schedule and
state, and from then on reports every change, and a heartbeat each minute.
The gateway answers the way OJ's gateway does, with the answers stored in the
thermostat's profile, and passes on what the thermostat reports.

The connection handling is adapted from peskyAdmin/schluter-local (MIT), see
the LICENSE file in this package.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ojmicroline_thermostat.exceptions import (
    OJMicrolineConnectionError,
    OJMicrolineError,
    OJMicrolineTimeoutError,
)

from .protobuf import FRAME_MARKER, MAX_FRAME, Message, encode_field, encode_frame
from .wg4 import (
    FIELD_ACKNOWLEDGED,
    FIELD_DEVICE_INFO,
    FIELD_KIND,
    FIELD_LOGIN,
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
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from .profile import DeviceProfile

_LOGGER = logging.getLogger(__name__)

# How long a thermostat gets to log in after it connected.
LOGIN_TIMEOUT = 30.0
# A thermostat sends a heartbeat every minute. After three missed ones the
# link is dead (power cut, Wi-Fi drop), even if the connection looks open.
IDLE_TIMEOUT = 180.0
# How long a thermostat gets to acknowledge a command.
ACKNOWLEDGE_TIMEOUT = 10.0

# Commands of the gateway count from here, which tells them apart from the
# messages of OJ's gateway in a log.
_FIRST_SEQUENCE = 101
# The length of a frame of at most MAX_FRAME bytes fits in three bytes.
_MAX_LENGTH_BYTES = 3
_STOP_TIMEOUT = 5.0

# Called with a serial number and what its thermostat reports, in the names
# and units of the thermostat JSON of the cloud API. "Online" tells whether
# the thermostat is connected to the gateway.
StateListener = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True)
class GatewayEndpoint:
    """Where the thermostats of a region look for their device gateway.

    Attributes
    ----------
        host: The hostname the thermostat connects to. A DNS record on the
            local network has to point it at the machine that runs the
            gateway.
        port: The port the thermostat connects to, so the gateway listens on
            it.
        upstream_ip: The address of OJ's own gateway, which the hostname no
            longer leads to.
        pin_sha256: The SHA-256 fingerprint of the certificate of OJ's
            gateway, in hexadecimal.

    """

    host: str
    port: int
    upstream_ip: str
    pin_sha256: str


WG4_NORTH_AMERICA = GatewayEndpoint(
    host="na-thermos.ojelectronics.com",
    port=9533,
    upstream_ip="52.165.43.242",
    pin_sha256="fcc865d1af114f0bdc3abb8677eca1edd9731a095b9e8525663c1a238c2bb03f",
)


def server_ssl_context(certfile: str | Path, keyfile: str | Path) -> ssl.SSLContext:
    """Create the TLS settings of the gateway from a certificate.

    The thermostat does not check the certificate, so a self-signed one is
    enough. This reads the files: call it from an executor, not from the event
    loop.

    Args:
    ----
        certfile: The path of the certificate, in PEM format.
        keyfile: The path of its private key, in PEM format.

    Returns:
    -------
        The context to create a WG4Gateway with.

    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        # The cipher suites the thermostat is known to accept come first.
        context.set_ciphers("AES256-SHA256:AES128-SHA256:AES256-SHA:AES128-SHA:DEFAULT")
    except ssl.SSLError:  # pragma: no cover - depends on the OpenSSL build
        context.set_ciphers("DEFAULT:@SECLEVEL=0")
    context.load_cert_chain(certfile, keyfile)
    return context


async def read_frame(reader: asyncio.StreamReader) -> bytes:
    """Read one frame from a connection and return its encoded message.

    Raises
    ------
        ValueError: The data is not a frame, or the frame is too large.
        asyncio.IncompleteReadError: The connection closed.

    """
    marker = (await reader.readexactly(1))[0]
    if marker != FRAME_MARKER:
        msg = f"A frame starts with {FRAME_MARKER:#04x}, not with {marker:#04x}"
        raise ValueError(msg)
    size = 0
    for index in range(_MAX_LENGTH_BYTES):
        byte = (await reader.readexactly(1))[0]
        size |= (byte & 0x7F) << (7 * index)
        if not byte & 0x80:
            break
    else:
        msg = f"The length of a frame takes at most {_MAX_LENGTH_BYTES} bytes"
        raise ValueError(msg)
    if size > MAX_FRAME:
        msg = f"A frame of {size} bytes exceeds the limit of {MAX_FRAME}"
        raise ValueError(msg)
    return await reader.readexactly(size)


@dataclass(eq=False)
class _Link:
    """The connection of one thermostat."""

    serial: str
    writer: asyncio.StreamWriter
    profile: DeviceProfile
    # The state and schedule the thermostat holds, as it sent them last.
    state: bytes | None = None
    schedule: bytes | None = None
    closed: bool = False
    # Commands that wait for the thermostat's answer, by sequence number.
    pending: dict[int, asyncio.Future[bool]] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class WG4Gateway:
    """Accepts the connections of WG4-series thermostats on the local network.

    Only thermostats in allowed_serials that have a profile are served; any
    other connection is closed without an answer. Set on_state to be told what
    the thermostats report.
    """

    def __init__(
        self,
        ssl_context: ssl.SSLContext,
        *,
        endpoint: GatewayEndpoint = WG4_NORTH_AMERICA,
        profiles: Mapping[str, DeviceProfile] | None = None,
        listen_host: str = "0.0.0.0",  # noqa: S104 - thermostats connect over the LAN
    ) -> None:
        """Create a gateway; it listens once start() is called.

        Args:
        ----
            ssl_context: The TLS settings, see server_ssl_context().
            endpoint: The device gateway of OJ that this gateway stands in for.
            profiles: The profile of each thermostat, by serial number.
            listen_host: The address to listen on; every IPv4 address by
                default.

        """
        self.endpoint = endpoint
        self.allowed_serials: set[str] = set()
        self.profiles: dict[str, DeviceProfile] = dict(profiles or {})
        self.on_state: StateListener | None = None
        self._ssl_context = ssl_context
        self._listen_host = listen_host
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._links: dict[str, _Link] = {}
        self._sequence = _FIRST_SEQUENCE - 1

    @property
    def port(self) -> int | None:
        """Return the port the gateway listens on, or None while it is stopped."""
        if self._server is None:
            return None
        port: int = self._server.sockets[0].getsockname()[1]
        return port

    @property
    def connected(self) -> frozenset[str]:
        """Return the serial numbers of the thermostats that are connected."""
        return frozenset(self._links)

    async def start(self) -> None:
        """Start listening for thermostats.

        Raises
        ------
            OJMicrolineConnectionError: The port cannot be listened on.

        """
        if self._server is not None:
            return
        try:
            self._server = await asyncio.start_server(
                self._accept,
                self._listen_host,
                self.endpoint.port,
                ssl=self._ssl_context,
                ssl_handshake_timeout=LOGIN_TIMEOUT,
                ssl_shutdown_timeout=_STOP_TIMEOUT,
            )
        except OSError as error:
            msg = f"Unable to listen on port {self.endpoint.port}: {error}"
            raise OJMicrolineConnectionError(msg) from error
        _LOGGER.debug("The gateway listens on port %s", self.port)

    async def stop(self) -> None:
        """Stop listening and close the connection of every thermostat."""
        server, self._server = self._server, None
        if server is None:
            return
        server.close()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # Every connection is closed by now; a stuck one must not hold this up.
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(_STOP_TIMEOUT):
                await server.wait_closed()

    async def set_mode(
        self, serial: str, mode: int, temperature: int | None = None
    ) -> None:
        """Put a thermostat in a regulation mode and wait until it confirms.

        The command is built from the state the thermostat reported last, so
        it only works for a connected thermostat that has uploaded its state.
        A command is never kept for later: a setpoint that is applied hours
        late does more harm than an error.

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
        link = self._links.get(serial)
        if link is None:
            msg = "The thermostat is not connected to the gateway."
            raise OJMicrolineConnectionError(msg)
        async with link.lock:
            if link.closed:
                msg = "The thermostat disconnected from the gateway."
                raise OJMicrolineConnectionError(msg)
            if link.state is None:
                msg = "The thermostat has not reported its state yet."
                raise OJMicrolineConnectionError(msg)
            state = changed_state(link.state, mode, temperature)
            await self._send_command(link, state)
            # The thermostat took the state over, so the next command starts
            # from it.
            link.state = state
        reported = Message.parse(encode_field(FIELD_STATE, state))
        self._emit(serial, decode_state(reported))

    async def _send_command(self, link: _Link, state: bytes) -> None:
        """Send a state to a thermostat and wait for its acknowledgement."""
        self._sequence += 1
        sequence = self._sequence
        waiting: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        link.pending[sequence] = waiting
        _LOGGER.debug("Sending command %s to thermostat %s", sequence, link.serial)
        try:
            link.writer.write(encode_frame(command(sequence, state, link.schedule)))
            await link.writer.drain()
            async with asyncio.timeout(ACKNOWLEDGE_TIMEOUT):
                acknowledged = await waiting
        except TimeoutError as error:
            msg = "The thermostat did not acknowledge the command."
            raise OJMicrolineTimeoutError(msg) from error
        except OSError as error:
            msg = "The thermostat disconnected before it acknowledged the command."
            raise OJMicrolineConnectionError(msg) from error
        finally:
            link.pending.pop(sequence, None)
        if not acknowledged:
            msg = "The thermostat refused the command."
            raise OJMicrolineError(msg)

    def _accept(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Serve a new connection in a task that stop() can cancel."""
        task = asyncio.create_task(self._handle(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        link: _Link | None = None
        try:
            link = await self._login(reader, writer)
            if link is not None:
                await self._serve(reader, link)
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            pass  # The thermostat hung up.
        except TimeoutError:
            _LOGGER.debug("Closing a connection that went quiet")
        except ValueError as error:
            _LOGGER.debug("Closing a connection after a malformed message: %s", error)
        except Exception:  # pylint: disable=broad-exception-caught
            # Whatever a thermostat sends, it must not take the gateway down.
            _LOGGER.exception("Unexpected error in the connection of a thermostat")
        finally:
            if link is not None:
                self._disconnected(link)
            writer.close()

    async def _login(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> _Link | None:
        """Wait for the login of a thermostat, and answer it if it is served."""
        async with asyncio.timeout(LOGIN_TIMEOUT):
            message = Message.parse(await read_frame(reader))
        serial = login_serial(message)
        if serial is None:
            msg = "The first message is not a login with a serial number"
            raise ValueError(msg)
        if serial not in self.allowed_serials:
            _LOGGER.debug("Refusing thermostat %s: it is not allowed", serial)
            return None
        profile = self.profiles.get(serial)
        if profile is None:
            _LOGGER.debug("Refusing thermostat %s: it has no profile", serial)
            return None
        link = _Link(serial, writer, profile)
        sequence = message.varint(FIELD_SEQUENCE) or 0
        writer.write(encode_frame(login_acknowledgement(sequence)))
        await writer.drain()
        self._connected(link)
        return link

    async def _serve(self, reader: asyncio.StreamReader, link: _Link) -> None:
        """Answer a thermostat the way OJ's gateway does, until it disconnects."""
        writer = link.writer
        while True:
            async with asyncio.timeout(IDLE_TIMEOUT):
                message = Message.parse(await read_frame(reader))
            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug("Thermostat %s: %s", link.serial, describe(message))
            if FIELD_LOGIN in message:
                msg = "A second login on the same connection"
                raise ValueError(msg)
            self._observe(link, message)
            sequence = message.varint(FIELD_SEQUENCE) or 0
            kind = message.varint(FIELD_KIND)
            if kind == KIND_SCHEDULE_UPLOAD:
                answer = encode_frame(acknowledgement(sequence)) + link.profile.support
            elif kind == KIND_STATE_UPLOAD:
                answer = (
                    encode_frame(acknowledgement(sequence)) + link.profile.configuration
                )
            elif FIELD_ACKNOWLEDGED in message and FIELD_KIND not in message:
                self._acknowledged(link, message)
                continue
            elif sequence:
                # A heartbeat, a change, or anything else that wants an answer.
                answer = encode_frame(acknowledgement(sequence))
            else:
                continue
            writer.write(answer)
            await writer.drain()

    def _observe(self, link: _Link, message: Message) -> None:
        """Keep what a message says about the thermostat, and pass it on."""
        schedule = message.data(FIELD_SCHEDULE)
        if schedule is not None:
            link.schedule = schedule
        state = message.data(FIELD_STATE)
        if state is not None:
            link.state = state
        if FIELD_STATE in message or FIELD_DEVICE_INFO in message:
            fields = decode_state(message)
        else:
            fields = decode_telemetry(message)
        if fields:
            self._emit(link.serial, fields)

    @staticmethod
    def _acknowledged(link: _Link, message: Message) -> None:
        """Hand the thermostat's answer to the command that waits for it."""
        sequence = message.varint(FIELD_SEQUENCE)
        waiting = None if sequence is None else link.pending.get(sequence)
        if waiting is not None and not waiting.done():
            waiting.set_result(message.varint(FIELD_ACKNOWLEDGED) == 1)

    def _connected(self, link: _Link) -> None:
        previous = self._links.get(link.serial)
        self._links[link.serial] = link
        if previous is not None:
            # The thermostat connected again, so its old connection is dead.
            self._close(previous)
        _LOGGER.debug("Thermostat %s connected to the gateway", link.serial)
        self._emit(link.serial, {"Online": True})

    def _disconnected(self, link: _Link) -> None:
        self._close(link)
        # A newer connection of the same thermostat keeps it online.
        if self._links.get(link.serial) is link:
            del self._links[link.serial]
            _LOGGER.debug("Thermostat %s disconnected from the gateway", link.serial)
            self._emit(link.serial, {"Online": False})

    @staticmethod
    def _close(link: _Link) -> None:
        link.closed = True
        error = ConnectionResetError("The thermostat disconnected")
        for waiting in link.pending.values():
            if not waiting.done():
                waiting.set_exception(error)
        link.writer.close()

    def _emit(self, serial: str, fields: dict[str, Any]) -> None:
        if self.on_state is None:
            return
        try:
            self.on_state(serial, fields)
        except Exception:  # pylint: disable=broad-exception-caught
            _LOGGER.exception("Error in the state listener of the gateway")
