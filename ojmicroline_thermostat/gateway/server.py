"""The listener that thermostats connect to instead of OJ's device gateway.

A thermostat that resolves the hostname of OJ's device gateway to this machine
connects here. It logs in with its serial number, uploads its schedule and
state, and from then on reports every change, and a heartbeat each minute.

In relay mode the gateway passes the connection on to OJ's gateway, so the
vendor's app keeps working. When OJ's gateway cannot be used, and always in
local mode, the gateway answers the thermostat itself. Either way it passes on
what the thermostat reports, and it can send the thermostat commands.

DeviceGateway holds what every series has in common. What a series says and
expects is in its own class: WG4Gateway and WD5Gateway.

The connection handling is adapted from peskyAdmin/schluter-local (MIT), see
the LICENSE file in this package.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import platform
import socket
import ssl
import struct
from abc import ABC, abstractmethod
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

from .protobuf import FRAME_MARKER, MAX_FRAME, Message, encode_frame

if TYPE_CHECKING:
    from pathlib import Path

_LOGGER = logging.getLogger(__name__)

# How long a thermostat gets to log in after it connected.
LOGIN_TIMEOUT = 30.0
# A thermostat sends a heartbeat every minute. After three missed ones the
# link is dead (power cut, Wi-Fi drop), even if the connection looks open.
IDLE_TIMEOUT = 180.0
# How long a thermostat gets to acknowledge a command.
ACKNOWLEDGE_TIMEOUT = 10.0
# How long OJ's gateway gets to accept a connection. A thermostat gives up on
# its login within a few seconds, so this has to stay well below that; a
# connection that works takes a fraction of a second.
UPSTREAM_TIMEOUT = 3.0
# How long OJ's gateway is left alone after it could not be reached, and how
# often it is tried again for a thermostat that is answered locally meanwhile.
UPSTREAM_RETRY_INTERVAL = 300.0

# Every series numbers its messages in field 1, and answers a message with
# that number and field 3: 1 accepts it.
_FIELD_SEQUENCE = 1
_FIELD_ANSWER = 3
_ACCEPTED = 1

# Commands of the gateway count from here, which tells them apart from the
# messages of OJ's gateway in a log.
_FIRST_SEQUENCE = 101
# The length of a frame of at most MAX_FRAME bytes fits in three bytes.
_MAX_LENGTH_BYTES = 3
_STOP_TIMEOUT = 5.0
# How many lines the history keeps: a few hours of one thermostat.
_HISTORY_SIZE = 300
# How many messages a thermostat may leave unanswered before the oldest is
# forgotten.
_MAX_UNANSWERED = 64
# The start of Linux's struct tcp_info: eight bytes, then 32-bit counters.
_TCP_INFO = struct.Struct("=8B24I")


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
# Called when an attempt to reach OJ's gateway ends differently than the last.
UpstreamListener = Callable[[UpstreamStatus], None]


@dataclass(frozen=True)
class GatewayEndpoint:
    """Where the thermostats of a region or brand look for their device gateway.

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

# WD5-series thermostats look for a hostname that holds the customer ID of
# their brand. The two names below lead to the same gateway, whose certificate
# is valid until 2044; address and fingerprint were read on 14 November 2026.
_WD5_ADDRESS = "40.68.93.45"
_WD5_PIN = "2c57953b72eb63c3266999ade32ce5857e0ea2867c4297cb254c397e4fa82e30"
# Customer 99, the SWATT app: seen in the DNS requests of an OWD5 thermostat.
WD5_R1099 = GatewayEndpoint(
    host="owd5-r1099-thermostat.ojelectronics.com",
    port=9513,
    upstream_ip=_WD5_ADDRESS,
    pin_sha256=_WD5_PIN,
)
# Customer 3, Schlüter in Europe: reported in the ioBroker adapter this
# package takes the WD5 protocol from.
WD5_SL003 = GatewayEndpoint(
    host="owd5-sl003-thermostat.ojelectronics.com",
    port=9513,
    upstream_ip=_WD5_ADDRESS,
    pin_sha256=_WD5_PIN,
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
        The context to create a gateway with.

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


def tcp_state(writer: asyncio.StreamWriter) -> str:
    """Describe the TCP connection behind a writer, where the system tells.

    Linux reports how much of what was sent is still unacknowledged and how
    often it was sent again. That tells a thermostat that is slow to answer
    from a network that does not deliver what was sent to it.

    Returns
    -------
        A text for a log line, or an empty text when nothing is known.

    """
    connection = writer.get_extra_info("socket")
    option = getattr(socket, "TCP_INFO", None)
    if connection is None or option is None or platform.system() != "Linux":
        return ""
    try:
        raw = connection.getsockopt(socket.IPPROTO_TCP, option, _TCP_INFO.size)
    except OSError:
        return ""
    if len(raw) < _TCP_INFO.size:
        return ""
    info = _TCP_INFO.unpack_from(raw)
    return (
        f"TCP: {info[12]} segments unacknowledged, the oldest sent again "
        f"{info[2]} times, {info[31]} retransmissions in total, last "
        f"acknowledgement {info[20] / 1000:.1f} s ago, retransmission timer "
        f"{info[8] / 1_000_000:.1f} s, round trip {info[23] / 1000:.0f} ms, "
        f"segment size {info[10]}"
    )


class _HandshakeError(Exception):
    """A connection did not get through the TLS handshake."""


@dataclass(eq=False)
class _Sent:
    """A message a thermostat was sent and still has to answer.

    For a command of the gateway it holds what to do once the thermostat
    accepts it, who waits for the answer, and when it was sent. A message of
    OJ's gateway has none of these: its answer is passed on.
    """

    sequence: int
    accept: Callable[[], None] | None = None
    waiting: asyncio.Future[bool] | None = None
    sent_at: float = 0.0


def _unanswered() -> deque[_Sent]:
    return deque(maxlen=_MAX_UNANSWERED)


@dataclass(eq=False)
class _Link:  # pylint: disable=too-many-instance-attributes
    """The connection of one thermostat."""

    serial: str
    writer: asyncio.StreamWriter
    # The connection to OJ's gateway, while the thermostat is relayed.
    upstream: asyncio.StreamWriter | None = None
    closed: bool = False
    # What the thermostat still has to answer, in the order it was sent. The
    # thermostat answers in that order, which tells whose answer it is when
    # OJ's gateway and this one used the same number.
    unanswered: deque[_Sent] = field(default_factory=_unanswered)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # The state and schedule the thermostat holds, as it sent them last.
    state: bytes | None = None
    schedule: bytes | None = None
    # WG4: the answers OJ's gateway gave on this connection, as frames.
    configuration: bytes | None = None
    support: bytes | None = None
    # WD5: whether the thermostat uploaded what it holds, the state of the
    # last command it was sent, and everything it reported so far.
    ready: bool = False
    template: bytes | None = None
    values: dict[str, Any] = field(default_factory=dict)


class DeviceGateway(ABC):  # pylint: disable=too-many-instance-attributes
    """Accepts the connections of thermostats on the local network.

    Only thermostats in allowed_serials are served; any other connection is
    closed without an answer.

    Set on_state to be told what the thermostats report, and on_upstream to
    follow whether OJ's gateway can be used.

    The gateway keeps a history of what happened on its connections, which is
    also logged at debug level. It names serial numbers and addresses on the
    local network, and never holds a login.
    """

    def __init__(
        self,
        ssl_context: ssl.SSLContext,
        *,
        mode: GatewayMode,
        endpoint: GatewayEndpoint,
        listen_host: str = "0.0.0.0",  # noqa: S104 - thermostats connect over the LAN
        listen_port: int | None = None,
    ) -> None:
        """Create a gateway; it listens once start() is called.

        Args:
        ----
            ssl_context: The TLS settings, see server_ssl_context().
            mode: Whether to relay the thermostats to OJ's gateway.
            endpoint: The device gateway of OJ that this gateway stands in for.
            listen_host: The address to listen on; every IPv4 address by
                default.
            listen_port: The port to listen on, if the thermostats reach this
                machine on another port than the endpoint's.

        """
        self.mode = mode
        self.endpoint = endpoint
        self.allowed_serials: set[str] = set()
        self.on_state: StateListener | None = None
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
        # When OJ's gateway could not be reached, on the clock of the loop.
        self._upstream_failed_at: float | None = None

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

    # --- What a series says and expects ---------------------------------------

    @abstractmethod
    def _login_serial(self, message: Message) -> str | None:
        """Return the serial number in a login; None if it is not a login."""

    @abstractmethod
    def _describe(self, message: Message) -> str:
        """Summarise a message for the log, without what must stay private."""

    @abstractmethod
    def _is_answer(self, message: Message) -> bool:
        """Tell whether a thermostat answers a message that was sent to it."""

    @abstractmethod
    def _expects_answer(self, message: Message) -> bool:
        """Tell whether a thermostat answers this message of OJ's gateway."""

    @abstractmethod
    def _can_answer(self, serial: str) -> bool:
        """Tell whether the gateway can answer this thermostat itself."""

    @abstractmethod
    def _greeting(self, login: Message) -> bytes:
        """Return the frames that answer a login when OJ's gateway does not."""

    @abstractmethod
    def _reply(self, link: _Link, message: Message) -> bytes | None:
        """Return the frames that answer a message of a thermostat, if any."""

    @abstractmethod
    def _observe(self, link: _Link, message: Message) -> None:
        """Keep what a thermostat reports, and pass it on to the listener."""

    @abstractmethod
    def _observe_upstream(self, link: _Link, frame: bytes, message: Message) -> None:
        """Keep what OJ's gateway sends a thermostat, and pass it on."""

    # --- Commands -------------------------------------------------------------

    def _link(self, serial: str) -> _Link:
        link = self._links.get(serial)
        if link is None:
            msg = "The thermostat is not connected to the gateway."
            raise OJMicrolineConnectionError(msg)
        return link

    async def _send_command(
        self,
        link: _Link,
        build: Callable[[int], bytes],
        accept: Callable[[], None],
    ) -> None:
        """Send a thermostat a command and wait for its answer.

        A thermostat can take longer to answer than a caller waits. Its answer
        still counts then: accept is called whenever the thermostat accepts
        the command, also after this gave up on it.

        Args:
        ----
            link: The connection of the thermostat.
            build: Returns the message, given its sequence number.
            accept: Called once the thermostat accepted the command.

        Raises:
        ------
            OJMicrolineConnectionError: The thermostat disconnected first.
            OJMicrolineTimeoutError: The thermostat did not answer in time.
            OJMicrolineError: The thermostat refused the command.

        """
        loop = asyncio.get_running_loop()
        sequence = self._next_sequence(link)
        frame = encode_frame(build(sequence))
        sent = _Sent(sequence, accept, loop.create_future(), loop.time())
        link.unanswered.append(sent)
        self._event(
            "Sending command %s to thermostat %s (%s bytes)",
            sequence,
            link.serial,
            len(frame),
        )
        try:
            link.writer.write(frame)
            await link.writer.drain()
            async with asyncio.timeout(ACKNOWLEDGE_TIMEOUT):
                accepted = await cast("asyncio.Future[bool]", sent.waiting)
        except TimeoutError as error:
            network = tcp_state(link.writer)
            self._event(
                "Thermostat %s did not answer command %s within %g seconds%s",
                link.serial,
                sequence,
                ACKNOWLEDGE_TIMEOUT,
                f" ({network})" if network else "",
            )
            msg = "The thermostat did not acknowledge the command."
            raise OJMicrolineTimeoutError(msg) from error
        except OSError as error:
            msg = "The thermostat disconnected before it acknowledged the command."
            raise OJMicrolineConnectionError(msg) from error
        if not accepted:
            msg = "The thermostat refused the command."
            raise OJMicrolineError(msg)

    def _next_sequence(self, link: _Link) -> int:
        """Return a number that nothing unanswered on this connection has."""
        taken = {sent.sequence for sent in link.unanswered}
        self._sequence += 1
        while self._sequence in taken:
            self._sequence += 1
        return self._sequence

    def _answered(self, link: _Link, message: Message) -> bool:
        """Match the answer of a thermostat with what it was sent.

        The thermostat answers in the order it received, so the answer belongs
        to the oldest unanswered message with its number.

        Returns
        -------
            Whether the answer is for a command of the gateway. Such an answer
            is not passed on: OJ's gateway never sent that command.

        """
        sequence = message.varint(_FIELD_SEQUENCE)
        sent = next((one for one in link.unanswered if one.sequence == sequence), None)
        if sent is None:
            return False
        link.unanswered.remove(sent)
        if sent.accept is None or sent.waiting is None:
            return False
        answer = message.varint(_FIELD_ANSWER)
        seconds = asyncio.get_running_loop().time() - sent.sent_at
        late = seconds > ACKNOWLEDGE_TIMEOUT
        if answer == _ACCEPTED:
            self._event(
                "Thermostat %s acknowledged command %s after %.1f seconds%s",
                link.serial,
                sequence,
                seconds,
                ", after the wait for it had ended" if late else "",
            )
            sent.accept()
        else:
            self._event(
                "Thermostat %s refused command %s after %.1f seconds (answer %s)",
                link.serial,
                sequence,
                seconds,
                answer,
            )
        if not sent.waiting.done():
            sent.waiting.set_result(answer == _ACCEPTED)
        return True

    # --- Connections ------------------------------------------------------------

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
            if self.mode is GatewayMode.RELAY and self._upstream_is_worth_trying(link):
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

    async def _read_login(self, reader: asyncio.StreamReader) -> tuple[str, bytes]:
        """Wait for the login of a thermostat; return its serial and the login."""
        async with asyncio.timeout(LOGIN_TIMEOUT):
            login = await read_frame(reader)
        serial = self._login_serial(Message.parse(login))
        if serial is None:
            msg = "The first message is not a login with a serial number"
            raise ValueError(msg)
        return serial, login

    # --- Answering a thermostat locally ---------------------------------------

    async def _answer(
        self, reader: asyncio.StreamReader, link: _Link, login: bytes
    ) -> None:
        """Answer a thermostat the way OJ's gateway does, if that is possible."""
        if not self._can_answer(link.serial):
            self._event(
                "Refusing thermostat %s: it has no profile to answer it with yet",
                link.serial,
            )
            return
        link.writer.write(self._greeting(Message.parse(login)))
        await link.writer.drain()
        self._connected(link)
        if self.mode is GatewayMode.LOCAL:
            await self._serve(reader, link)
        else:
            # The thermostat should be relayed: keep trying OJ's gateway.
            retry = asyncio.create_task(self._retry_upstream(link))
            self._track(retry)
            try:
                await self._serve(reader, link)
            finally:
                retry.cancel()

    async def _serve(self, reader: asyncio.StreamReader, link: _Link) -> None:
        writer = link.writer
        while True:
            async with asyncio.timeout(IDLE_TIMEOUT):
                message = Message.parse(await read_frame(reader))
            self._log("Thermostat", link, message)
            if self._login_serial(message) is not None:
                msg = "A second login on the same connection"
                raise ValueError(msg)
            if self._is_answer(message):
                self._answered(link, message)
                continue
            self._observe(link, message)
            answer = self._reply(link, message)
            if answer:
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

    def _upstream_is_worth_trying(self, link: _Link) -> bool:
        """Tell whether a new connection should wait for OJ's gateway.

        A thermostat gives up on its login within a few seconds and connects
        again. Waiting for a gateway that was unreachable a moment ago would
        keep it from ever being answered, so it is answered locally at once.
        """
        if self._upstream_failed_at is None or not self._can_answer(link.serial):
            # Without a local answer OJ's gateway is the only way.
            return True
        waited = asyncio.get_running_loop().time() - self._upstream_failed_at
        if waited >= UPSTREAM_RETRY_INTERVAL:
            return True
        self._event(
            "Not waiting for OJ's gateway for thermostat %s: it was unreachable "
            "%.0f seconds ago",
            link.serial,
            waited,
        )
        return False

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
            self._upstream_failed_at = asyncio.get_running_loop().time()
            self._set_upstream(UpstreamStatus.UNREACHABLE)
            return None
        self._upstream_failed_at = None
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
            if self._login_serial(message) is not None:
                msg = "A second login on the same connection"
                raise ValueError(msg)
            if self._is_answer(message):
                if self._answered(link, message):
                    # OJ's gateway never sent the command this answers.
                    continue
            else:
                self._observe(link, message)
            upstream.write(encode_frame(body))
            await upstream.drain()

    async def _pass_down(self, upstream: asyncio.StreamReader, link: _Link) -> None:
        """Pass the messages of OJ's gateway on to a thermostat."""
        while True:
            body = await read_frame(upstream)
            frame = encode_frame(body)
            try:
                message = Message.parse(body)
            except ValueError:
                message = None
            if message is not None and self._expects_answer(message):
                # Noted before it is sent, so the order of the answers holds.
                number = message.varint(_FIELD_SEQUENCE)
                link.unanswered.append(_Sent(0 if number is None else number))
            link.writer.write(frame)
            await link.writer.drain()
            # The message is on its way; what follows only looks at it.
            if message is None or not self._inspect(link, frame, message):
                self._event(
                    "OJ's gateway sent thermostat %s a message of %s bytes that is "
                    "not understood",
                    link.serial,
                    len(body),
                )

    def _inspect(self, link: _Link, frame: bytes, message: Message) -> bool:
        """Look at a message of OJ's gateway; False if it cannot be decoded."""
        self._log("OJ's gateway", link, message)
        try:
            # A change made in the vendor's app arrives as a command.
            self._observe_upstream(link, frame, message)
        except ValueError:
            return False
        return True

    # --- What both ways of serving share --------------------------------------

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
        for sent in link.unanswered:
            if sent.waiting is not None and not sent.waiting.done():
                sent.waiting.set_exception(error)
        link.writer.close()
        if link.upstream is not None:
            link.upstream.close()

    def _log(self, sender: str, link: _Link, message: Message) -> None:
        self._event(
            "%s (thermostat %s): %s", sender, link.serial, self._describe(message)
        )

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
