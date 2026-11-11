"""A fake thermostat and a fake OJ gateway for the tests of the device gateway.

Both talk TLS over the loopback interface. Like the real thermostat, the fake
one does not check the certificate of what it connects to.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ssl
from typing import TYPE_CHECKING, Any

import trustme

from ojmicroline_thermostat.gateway import WG4_NORTH_AMERICA, DeviceProfile
from ojmicroline_thermostat.gateway.protobuf import Message, encode_field, encode_frame
from ojmicroline_thermostat.gateway.server import read_frame
from ojmicroline_thermostat.gateway.wg4 import login_acknowledgement

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

SERIAL = "1234567"

# The two answers of OJ's gateway as a caller has them stored, with made-up
# content.
PROFILE = DeviceProfile(
    configuration=encode_frame(
        encode_field(1, 1) + encode_field(5, 1) + encode_field(10, encode_field(1, 1))
    ),
    support=encode_frame(
        encode_field(1, 0) + encode_field(5, 1) + encode_field(18, b"Example support")
    ),
)
# The same answers as the fake OJ gateway gives them, to tell the two apart.
CLOUD_PROFILE = DeviceProfile(
    configuration=encode_frame(
        encode_field(1, 1) + encode_field(5, 1) + encode_field(10, encode_field(1, 2))
    ),
    support=encode_frame(
        encode_field(1, 0) + encode_field(5, 1) + encode_field(18, b"Cloud support")
    ),
)

# What a thermostat sends after it connected: its login, the upload of its
# schedule and settings, and the full upload with its state. Setpoints are in
# 1/200 °C: a temporary hold at 16.66 °C and a permanent one at 22.22 °C.
STATE = (
    encode_field(1, 1000)
    + encode_field(2, 3)
    + encode_field(4, encode_field(1, 3332))
    + encode_field(5, encode_field(1, 4444))
    + encode_field(6, 0)
)
SCHEDULE = b"\x08\x01"
SCHEDULE_UPLOAD = encode_field(1, 30) + encode_field(2, 6)
STATE_UPLOAD = (
    encode_field(1, 31)
    + encode_field(2, 4)
    + encode_field(5, 1)
    + encode_field(7, encode_field(2, b"Kitchen Floor"))
    + encode_field(8, SCHEDULE)
    + encode_field(9, STATE)
)
# What the gateway reports after the full upload.
UPLOADED = {
    "RegulationMode": 3,
    "ComfortTemperature": 1666,
    "ManualTemperature": 2222,
    "SetPointTemp": 2222,
    "Room": "Kitchen Floor",
}

Events = list[tuple[str, dict[str, Any]]]


def login(serial: str = SERIAL) -> bytes:
    """Build a login like the thermostat's, with a made-up email address."""
    account = (
        encode_field(1, b"user@example.com")
        + encode_field(2, serial.encode())
        + encode_field(3, 10207)
    )
    return encode_field(1, 29) + encode_field(5, 1) + encode_field(6, account)


def acknowledgement(sequence: int, accepted: int = 1) -> bytes:
    """Build the answer to a message with a sequence number."""
    return encode_field(1, sequence) + encode_field(3, accepted)


async def until(condition: Callable[[], bool]) -> None:
    """Wait until the other side has caught up."""
    async with asyncio.timeout(2):
        while not condition():  # noqa: ASYNC110 - there is no event to wait for
            await asyncio.sleep(0.005)


def issue_certificate(directory: Path, name: str) -> tuple[Path, Path, str]:
    """Create a certificate with an RSA key, as the thermostat's TLS needs.

    Returns the paths of the certificate and its key, and the SHA-256
    fingerprint of the certificate.
    """
    authority = trustme.CA(key_type=trustme.KeyType.RSA)
    issued = authority.issue_cert(WG4_NORTH_AMERICA.host, key_type=trustme.KeyType.RSA)
    certfile = directory / f"{name}.crt"
    keyfile = directory / f"{name}.key"
    issued.cert_chain_pems[0].write_to_path(certfile)
    issued.private_key_pem.write_to_path(keyfile)
    der = ssl.PEM_cert_to_DER_cert(issued.cert_chain_pems[0].bytes().decode())
    return certfile, keyfile, hashlib.sha256(der).hexdigest()


class FakeThermostat:
    """The thermostat's side of a connection to the gateway."""

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Wrap an open connection."""
        self._reader = reader
        self._writer = writer

    @classmethod
    async def connect(
        cls, port: int | None, context: ssl.SSLContext | None = None
    ) -> FakeThermostat:
        """Connect to a gateway without checking its certificate."""
        if context is None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=context)
        return cls(reader, writer)

    @property
    def cipher(self) -> str:
        """Return the name of the cipher suite of the connection."""
        name: str = self._writer.get_extra_info("cipher")[0]
        return name

    async def send(self, message: bytes) -> None:
        """Send a message in a frame."""
        await self.send_raw(encode_frame(message))

    async def send_raw(self, data: bytes) -> None:
        """Send bytes as they are."""
        self._writer.write(data)
        await self._writer.drain()

    async def receive_frame(self) -> bytes:
        """Wait for the next frame and return it whole."""
        async with asyncio.timeout(2):
            return encode_frame(await read_frame(self._reader))

    async def receive(self) -> Message:
        """Wait for the next message."""
        async with asyncio.timeout(2):
            return Message.parse(await read_frame(self._reader))

    async def login(self, serial: str = SERIAL) -> Message:
        """Log in and return the answer."""
        await self.send(login(serial))
        return await self.receive()

    async def upload(self) -> list[bytes]:
        """Upload the schedule and the state, as a thermostat does after login.

        Returns the four frames that answer the two uploads.
        """
        await self.send(SCHEDULE_UPLOAD)
        answers = [await self.receive_frame(), await self.receive_frame()]
        await self.send(STATE_UPLOAD)
        return [*answers, await self.receive_frame(), await self.receive_frame()]

    async def was_closed(self) -> bool:
        """Tell whether the other side hung up without sending anything more."""
        async with asyncio.timeout(2):
            return await self._reader.read(1) == b""

    async def close(self) -> None:
        """Hang up."""
        self._writer.close()
        # The other side may have hung up first, which is fine.
        with contextlib.suppress(ConnectionError, ssl.SSLError, TimeoutError):
            async with asyncio.timeout(2):
                await self._writer.wait_closed()


class FakeCloud:
    """OJ's device gateway, as far as a relay needs it.

    It answers a thermostat the way the real one does, and keeps every message
    it receives so a test can tell what the relay passed on.
    """

    def __init__(self, context: ssl.SSLContext, fingerprint: str) -> None:
        """Create a gateway that presents the certificate of the context."""
        self.fingerprint = fingerprint
        self.profile = CLOUD_PROFILE
        self.received: list[Message] = []
        self.connections = 0
        self.hung_up = 0
        self._context = context
        self._server: asyncio.Server | None = None
        self._writers: list[asyncio.StreamWriter] = []

    @property
    def port(self) -> int:
        """Return the port the gateway listens on."""
        assert self._server is not None
        port: int = self._server.sockets[0].getsockname()[1]
        return port

    async def start(self, port: int = 0) -> None:
        """Start listening, on a free port unless one is given."""
        self._server = await asyncio.start_server(
            self._handle, "127.0.0.1", port, ssl=self._context
        )

    async def stop(self) -> None:
        """Stop listening and hang up."""
        if self._server is not None:
            self._server.close()
            self._server = None
        await self.hang_up()

    async def hang_up(self) -> None:
        """Close every connection, as OJ's gateway or the network may do."""
        for writer in self._writers:
            writer.close()
        self._writers.clear()

    async def send(self, message: bytes) -> None:
        """Send a message over the newest connection, like an app change."""
        writer = self._writers[-1]
        writer.write(encode_frame(message))
        await writer.drain()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.connections += 1
        self._writers.append(writer)
        try:
            while True:
                message = Message.parse(await read_frame(reader))
                self.received.append(message)
                sequence = message.varint(1) or 0
                kind = message.varint(2)
                if 6 in message:
                    answer = encode_frame(login_acknowledgement(sequence))
                elif kind == 6:
                    answer = (
                        encode_frame(acknowledgement(sequence)) + self.profile.support
                    )
                elif kind == 4:
                    answer = (
                        encode_frame(acknowledgement(sequence))
                        + self.profile.configuration
                    )
                elif (3 in message and 2 not in message) or not sequence:
                    continue
                else:
                    answer = encode_frame(acknowledgement(sequence))
                writer.write(answer)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            pass
        finally:
            self.hung_up += 1
            writer.close()
