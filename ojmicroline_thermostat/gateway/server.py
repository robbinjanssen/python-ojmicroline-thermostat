"""The listener that WG4-series thermostats connect to.

A thermostat that resolves the hostname of OJ's device gateway to this machine
connects here. It logs in with its serial number, uploads its schedule and
state, and from then on reports every change, and a heartbeat each minute.

In relay mode the gateway passes the connection on to OJ's gateway, so the
vendor's app keeps working, and it records the answers OJ's gateway gives the
thermostat as the thermostat's profile. When OJ's gateway cannot be used, and
always in local mode, the gateway answers the thermostat itself, with that
profile. Either way it passes on what the thermostat reports, and it can send
the thermostat commands.

The connection handling is adapted from peskyAdmin/schluter-local (MIT), see
the LICENSE file in this package.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import ssl
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, cast

from ojmicroline_thermostat.exceptions import (
    OJMicrolineConnectionError,
    OJMicrolineError,
    OJMicrolineTimeoutError,
)

from .profile import DeviceProfile
from .protobuf import FRAME_MARKER, MAX_FRAME, Message, encode_field, encode_frame
from .wg4 import (
    FIELD_ACKNOWLEDGED,
    FIELD_CONFIGURATION,
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
    response_field,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

_LOGGER = logging.getLogger(__name__)

# How long a thermostat gets to log in after it connected.
LOGIN_TIMEOUT = 30.0
# A thermostat sends a heartbeat every minute. After three missed ones the
# link is dead (power cut, Wi-Fi drop), even if the connection looks open.
IDLE_TIMEOUT = 180.0
# How long a thermostat gets to acknowledge a command.
ACKNOWLEDGE_TIMEOUT = 10.0
# How long OJ's gateway gets to accept a connection.
UPSTREAM_TIMEOUT = 10.0
# How often OJ's gateway is tried again while a thermostat that should be
# relayed is answered locally.
UPSTREAM_RETRY_INTERVAL = 300.0

# Commands of the gateway count from here, which tells them apart from the
# messages of OJ's gateway in a log.
_FIRST_SEQUENCE = 101
# The length of a frame of at most MAX_FRAME bytes fits in three bytes.
_MAX_LENGTH_BYTES = 3
_STOP_TIMEOUT = 5.0
# How many lines the history keeps: a few hours of one thermostat.
_HISTORY_SIZE = 300


class GatewayMode(StrEnum):
    """What the gateway does with the connection of a thermostat."""

    # Pass it on to OJ's gateway, and answer locally when that cannot be used.
    RELAY = "relay"
    # Answer locally; OJ's gateway is never contacted.
    LOCAL = "local"


class UpstreamStatus(StrEnum):
    """How the last attempt to reach OJ's gateway went."""

    CONNECTED = "connected"
    UNREACHABLE = "unreachable"
    # Its certificate does not match the pin, so nothing is sent to it.
    UNTRUSTED = "untrusted"


# Called with a serial number and what its thermostat reports, in the names
# and units of the thermostat JSON of the cloud API. "Online" tells whether
# the thermostat is connected to the gateway.
StateListener = Callable[[str, dict[str, Any]], None]
# Called with a serial number and the profile recorded for its thermostat.
ProfileListener = Callable[[str, DeviceProfile], None]
# Called when an attempt to reach OJ's gateway ends differently than the last.
UpstreamListener = Callable[[UpstreamStatus], None]


@dataclass(frozen=True)
class GatewayEndpoint:
    """Where the thermostats of a region look for their device gateway.

    Attributes
    ----------
        host: The hostname the thermostat connects to. A DNS record on the
            local network has to point it at the machine that runs the
            gateway.
        port: The port the thermostat connects to.
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


def _upstream_ssl_context() -> ssl.SSLContext:
    """Create the TLS settings for the connection to OJ's gateway.

    The certificate of OJ's gateway is self-signed, so no certificate
    authority vouches for it. The gateway compares its fingerprint with a pin
    instead, right after the handshake and before anything is sent.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_2
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


class _HandshakeError(Exception):
    """A connection did not get through the TLS handshake."""


@dataclass(eq=False)
class _Link:
    """The connection of one thermostat."""

    serial: str
    writer: asyncio.StreamWriter
    # The connection to OJ's gateway, while the thermostat is relayed.
    upstream: asyncio.StreamWriter | None = None
    # The answers OJ's gateway gave on this connection, as complete frames.
    configuration: bytes | None = None
    support: bytes | None = None
    # The state and schedule the thermostat holds, as it sent them last.
    state: bytes | None = None
    schedule: bytes | None = None
    closed: bool = False
    # The sequence numbers of the commands the gateway sent that are not
    # answered yet, and the commands that still wait for that answer.
    sent: set[int] = field(default_factory=set)
    pending: dict[int, asyncio.Future[bool]] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class WG4Gateway:  # pylint: disable=too-many-instance-attributes
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
        self.mode = mode
        self.endpoint = endpoint
        self.allowed_serials: set[str] = set()
        self.profiles: dict[str, DeviceProfile] = dict(profiles or {})
        self.on_state: StateListener | None = None
        self.on_profile: ProfileListener | None = None
        self.on_upstream: UpstreamListener | None = None
        self.upstream_status: UpstreamStatus | None = None
        # The fingerprint of the certificate OJ's gateway showed last.
        self.upstream_fingerprint: str | None = None
        self._history: deque[str] = deque(maxlen=_HISTORY_SIZE)
        self._ssl_context = ssl_context
        self._upstream_ssl_context = _upstream_ssl_context()
        self._listen_host = listen_host
        self._listen_port = endpoint.port if listen_port is None else listen_port
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
    def history(self) -> tuple[str, ...]:
        """Return the most recent events, oldest first, each with its UTC time."""
        return tuple(self._history)

    @property
    def connected(self) -> frozenset[str]:
        """Return the serial numbers of the thermostats that are connected."""
        return frozenset(self._links)

    def is_relayed(self, serial: str) -> bool:
        """Return whether a thermostat is connected to OJ's gateway as well."""
        link = self._links.get(serial)
        return link is not None and link.upstream is not None

    async def start(self) -> None:
        """Start listening for thermostats.

        Raises
        ------
            OJMicrolineConnectionError: The port cannot be listened on.

        """
        if self._server is not None:
            return
        try:
            # TLS starts in _secure(), so a handshake that fails can be told.
            self._server = await asyncio.start_server(
                self._accept, self._listen_host, self._listen_port
            )
        except OSError as error:
            msg = f"Unable to listen on port {self._listen_port}: {error}"
            raise OJMicrolineConnectionError(msg) from error
        self._event("The gateway listens on port %s (%s mode)", self.port, self.mode)

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

        The command goes straight to the thermostat, also while it is relayed.

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
        self._notify(self.on_state, serial, decode_state(reported))

    async def _send_command(self, link: _Link, state: bytes) -> None:
        """Send a state to a thermostat and wait for its acknowledgement."""
        self._sequence += 1
        sequence = self._sequence
        waiting: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        link.sent.add(sequence)
        link.pending[sequence] = waiting
        self._event("Sending command %s to thermostat %s", sequence, link.serial)
        try:
            link.writer.write(encode_frame(command(sequence, state, link.schedule)))
            await link.writer.drain()
            async with asyncio.timeout(ACKNOWLEDGE_TIMEOUT):
                acknowledged = await waiting
        except TimeoutError as error:
            self._event(
                "Thermostat %s did not answer command %s", link.serial, sequence
            )
            msg = "The thermostat did not acknowledge the command."
            raise OJMicrolineTimeoutError(msg) from error
        except OSError as error:
            msg = "The thermostat disconnected before it acknowledged the command."
            raise OJMicrolineConnectionError(msg) from error
        finally:
            link.pending.pop(sequence, None)
        if not acknowledged:
            self._event("Thermostat %s refused command %s", link.serial, sequence)
            msg = "The thermostat refused the command."
            raise OJMicrolineError(msg)
        self._event("Thermostat %s acknowledged command %s", link.serial, sequence)

    def _accept(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Serve a new connection in a task that stop() can cancel."""
        # Nothing may be read before TLS has started, or the start of the
        # handshake would be taken for a message.
        cast("asyncio.Transport", writer.transport).pause_reading()
        self._track(asyncio.create_task(self._handle(reader, writer)))

    def _track(self, task: asyncio.Task[None]) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        who = str(peer[0]) if peer else "an unknown address"
        link: _Link | None = None
        try:
            await self._secure(writer, who)
            serial, login = await self._read_login(reader)
            who = f"thermostat {serial}"
            if serial not in self.allowed_serials:
                self._event("Refusing %s: it is not on the account", who)
                return
            link = _Link(serial, writer)
            upstream = None
            if self.mode is GatewayMode.RELAY:
                upstream = await self._open_upstream()
            if upstream is None:
                await self._answer(reader, link, login)
            else:
                await self._relay(reader, link, login, upstream)
        except _HandshakeError:
            pass  # _secure() told why.
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            self._event("The connection of %s was closed", who)
        except TimeoutError:
            self._event("Closing the connection of %s: it went quiet", who)
        except ValueError as error:
            self._event(
                "Closing the connection of %s after a malformed message: %s", who, error
            )
        except Exception:  # pylint: disable=broad-exception-caught
            # Whatever a thermostat sends, it must not take the gateway down.
            _LOGGER.exception("Unexpected error in the connection of a thermostat")
        finally:
            if link is not None:
                self._disconnected(link)
            writer.close()

    @staticmethod
    async def _read_login(reader: asyncio.StreamReader) -> tuple[str, bytes]:
        """Wait for the login of a thermostat; return its serial and the login."""
        async with asyncio.timeout(LOGIN_TIMEOUT):
            login = await read_frame(reader)
        serial = login_serial(Message.parse(login))
        if serial is None:
            msg = "The first message is not a login with a serial number"
            raise ValueError(msg)
        return serial, login

    # --- Answering a thermostat locally ---------------------------------------

    async def _answer(
        self, reader: asyncio.StreamReader, link: _Link, login: bytes
    ) -> None:
        """Answer a thermostat the way OJ's gateway does, if it has a profile."""
        profile = self.profiles.get(link.serial)
        if profile is None:
            self._event(
                "Refusing thermostat %s: it has no profile to answer it with yet",
                link.serial,
            )
            return
        sequence = Message.parse(login).varint(FIELD_SEQUENCE) or 0
        link.writer.write(encode_frame(login_acknowledgement(sequence)))
        await link.writer.drain()
        self._connected(link)
        if self.mode is GatewayMode.LOCAL:
            await self._serve(reader, link, profile)
        else:
            # The thermostat should be relayed: keep trying OJ's gateway.
            retry = asyncio.create_task(self._retry_upstream(link))
            self._track(retry)
            try:
                await self._serve(reader, link, profile)
            finally:
                retry.cancel()

    async def _serve(
        self, reader: asyncio.StreamReader, link: _Link, profile: DeviceProfile
    ) -> None:
        writer = link.writer
        while True:
            async with asyncio.timeout(IDLE_TIMEOUT):
                message = Message.parse(await read_frame(reader))
            self._log("Thermostat", link, message)
            if FIELD_LOGIN in message:
                msg = "A second login on the same connection"
                raise ValueError(msg)
            self._observe(link, message)
            sequence = message.varint(FIELD_SEQUENCE) or 0
            kind = message.varint(FIELD_KIND)
            if kind == KIND_SCHEDULE_UPLOAD:
                answer = encode_frame(acknowledgement(sequence)) + profile.support
            elif kind == KIND_STATE_UPLOAD:
                answer = encode_frame(acknowledgement(sequence)) + profile.configuration
            elif self._is_answer(message):
                self._acknowledged(link, message)
                continue
            elif sequence:
                # A heartbeat, a change, or anything else that wants an answer.
                answer = encode_frame(acknowledgement(sequence))
            else:
                continue
            writer.write(answer)
            await writer.drain()

    async def _retry_upstream(self, link: _Link) -> None:
        """Make a thermostat connect again once OJ's gateway can be used."""
        upstream = None
        while upstream is None:
            await asyncio.sleep(UPSTREAM_RETRY_INTERVAL)
            upstream = await self._open_upstream()
        upstream[1].close()
        self._event(
            "OJ's gateway can be used again; reconnecting thermostat %s", link.serial
        )
        # The thermostat connects again by itself, and is relayed then.
        link.writer.close()

    # --- Relaying a thermostat to OJ's gateway --------------------------------

    async def _open_upstream(
        self,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter] | None:
        """Connect to OJ's gateway; None if it is unreachable or not trusted."""
        try:
            async with asyncio.timeout(UPSTREAM_TIMEOUT):
                reader, writer = await asyncio.open_connection(
                    self.endpoint.upstream_ip,
                    self.endpoint.port,
                    ssl=self._upstream_ssl_context,
                    server_hostname=self.endpoint.host,
                )
        except OSError as error:
            # Unreachable, refused, timed out, or the TLS handshake failed.
            self._event(
                "OJ's gateway at %s cannot be used: %r",
                self.endpoint.upstream_ip,
                error,
            )
            self._set_upstream(UpstreamStatus.UNREACHABLE)
            return None
        certificate = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
        pin = self.endpoint.pin_sha256.replace(":", "").lower()
        self.upstream_fingerprint = hashlib.sha256(certificate or b"").hexdigest()
        if self.upstream_fingerprint != pin:
            writer.close()
            self._set_upstream(UpstreamStatus.UNTRUSTED)
            return None
        self._set_upstream(UpstreamStatus.CONNECTED)
        return reader, writer

    def _set_upstream(self, status: UpstreamStatus) -> None:
        if status is self.upstream_status:
            return
        self.upstream_status = status
        if status is UpstreamStatus.UNTRUSTED:
            self._event(
                "The certificate of OJ's gateway at %s does not match the pin, so "
                "nothing is sent to it. Its SHA-256 fingerprint is %s",
                self.endpoint.upstream_ip,
                self.upstream_fingerprint,
                level=logging.WARNING,
            )
        else:
            self._event(
                "OJ's gateway at %s is %s", self.endpoint.upstream_ip, status.value
            )
        self._notify(self.on_upstream, status)

    async def _relay(
        self,
        reader: asyncio.StreamReader,
        link: _Link,
        login: bytes,
        upstream: tuple[asyncio.StreamReader, asyncio.StreamWriter],
    ) -> None:
        """Pass a connection on to OJ's gateway, until either side closes it."""
        upstream_reader, upstream_writer = upstream
        link.upstream = upstream_writer
        upstream_writer.write(encode_frame(login))
        await upstream_writer.drain()
        self._connected(link)
        pumps = [
            asyncio.create_task(self._pass_up(reader, link, upstream_writer)),
            asyncio.create_task(self._pass_down(upstream_reader, link)),
        ]
        try:
            done, _ = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for pump in pumps:
                pump.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
        # A pump only ends with an error: the one that ended the relay.
        next(iter(done)).result()

    async def _pass_up(
        self,
        reader: asyncio.StreamReader,
        link: _Link,
        upstream: asyncio.StreamWriter,
    ) -> None:
        """Pass the messages of a thermostat on to OJ's gateway."""
        while True:
            async with asyncio.timeout(IDLE_TIMEOUT):
                body = await read_frame(reader)
            message = Message.parse(body)
            self._log("Thermostat", link, message)
            if FIELD_LOGIN in message:
                msg = "A second login on the same connection"
                raise ValueError(msg)
            if self._is_answer(message) and self._acknowledged(link, message):
                # OJ's gateway never sent the command this answers.
                continue
            self._observe(link, message)
            upstream.write(encode_frame(body))
            await upstream.drain()

    async def _pass_down(self, upstream: asyncio.StreamReader, link: _Link) -> None:
        """Pass the messages of OJ's gateway on to a thermostat."""
        while True:
            body = await read_frame(upstream)
            link.writer.write(encode_frame(body))
            await link.writer.drain()
            # The message is on its way; what follows only looks at it.
            try:
                message = Message.parse(body)
                self._log("OJ's gateway", link, message)
                self._record(link, encode_frame(body), message)
                # A change made in the vendor's app arrives as a command.
                self._observe(link, message)
            except ValueError:
                self._event(
                    "OJ's gateway sent thermostat %s a message of %s bytes that is "
                    "not understood",
                    link.serial,
                    len(body),
                )

    def _record(self, link: _Link, frame: bytes, message: Message) -> None:
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

    # --- What both ways of serving share --------------------------------------

    @staticmethod
    def _is_answer(message: Message) -> bool:
        """Tell whether a thermostat answers a message that was sent to it."""
        return FIELD_ACKNOWLEDGED in message and FIELD_KIND not in message

    @staticmethod
    def _acknowledged(link: _Link, message: Message) -> bool:
        """Hand the thermostat's answer to the command that waits for it.

        Returns whether the answer is for a command of the gateway. Each
        command takes one answer, so an answer with the same number for OJ's
        gateway is not held back.
        """
        sequence = message.varint(FIELD_SEQUENCE)
        if sequence is None or sequence not in link.sent:
            return False
        link.sent.discard(sequence)
        waiting = link.pending.get(sequence)
        if waiting is not None and not waiting.done():
            waiting.set_result(message.varint(FIELD_ACKNOWLEDGED) == 1)
        return True

    def _observe(self, link: _Link, message: Message) -> None:
        """Keep what a message says about the thermostat, and pass it on."""
        # Decoding comes first: it refuses a malformed state, which must not
        # become the state that the next command starts from.
        if FIELD_STATE in message or FIELD_DEVICE_INFO in message:
            fields = decode_state(message)
        else:
            fields = decode_telemetry(message)
        schedule = message.data(FIELD_SCHEDULE)
        if schedule is not None:
            link.schedule = schedule
        state = message.data(FIELD_STATE)
        if state is not None:
            link.state = state
        if fields:
            self._notify(self.on_state, link.serial, fields)

    def _connected(self, link: _Link) -> None:
        previous = self._links.get(link.serial)
        self._links[link.serial] = link
        if previous is not None:
            # The thermostat connected again, so its old connection is dead.
            self._close(previous)
        self._event(
            "Thermostat %s logged in; it is %s",
            link.serial,
            "relayed to OJ's gateway"
            if link.upstream is not None
            else "answered locally",
        )
        self._notify(self.on_state, link.serial, {"Online": True})

    def _disconnected(self, link: _Link) -> None:
        self._close(link)
        # A newer connection of the same thermostat keeps it online.
        if self._links.get(link.serial) is link:
            del self._links[link.serial]
            self._event("Thermostat %s is offline", link.serial)
            self._notify(self.on_state, link.serial, {"Online": False})

    @staticmethod
    def _close(link: _Link) -> None:
        link.closed = True
        error = ConnectionResetError("The thermostat disconnected")
        for waiting in link.pending.values():
            if not waiting.done():
                waiting.set_exception(error)
        link.writer.close()
        if link.upstream is not None:
            link.upstream.close()

    def _log(self, sender: str, link: _Link, message: Message) -> None:
        self._event("%s (thermostat %s): %s", sender, link.serial, describe(message))

    def _event(
        self, message: str, *arguments: object, level: int = logging.DEBUG
    ) -> None:
        """Log what happened on a connection, and keep it in the history."""
        _LOGGER.log(level, message, *arguments)
        moment = datetime.now(tz=UTC)
        self._history.append(f"{moment:%Y-%m-%d %H:%M:%S} {message % arguments}")

    async def _secure(self, writer: asyncio.StreamWriter, address: str) -> None:
        """Start TLS on a new connection, and tell how that went.

        Whether a thermostat accepts the gateway's certificate and cipher
        suites shows here, so both outcomes are worth an event.
        """
        try:
            await writer.start_tls(
                self._ssl_context, ssl_handshake_timeout=LOGIN_TIMEOUT
            )
        except OSError as error:
            self._event("The TLS handshake with %s failed: %r", address, error)
            raise _HandshakeError from error
        cipher = writer.get_extra_info("cipher")
        self._event(
            "Connection from %s, secured with %s (%s)", address, cipher[1], cipher[0]
        )

    @staticmethod
    def _notify(listener: Callable[..., None] | None, *arguments: Any) -> None:
        """Call a listener, which must not be able to break a connection."""
        if listener is None:
            return
        try:
            listener(*arguments)
        except Exception:  # pylint: disable=broad-exception-caught
            _LOGGER.exception("Error in a listener of the gateway")
