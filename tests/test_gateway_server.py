"""Test the gateway that WG4-series thermostats connect to.

A fake thermostat talks to a real gateway over TLS on the loopback interface.
These tests cover a gateway that answers the thermostat itself; the tests of
the relay are in test_gateway_relay.py.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import socket
import ssl
import struct
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from ojmicroline_thermostat import (
    OJMicrolineConnectionError,
    OJMicrolineError,
    OJMicrolineTimeoutError,
)
from ojmicroline_thermostat.gateway import (
    GatewayMode,
    WG4Gateway,
    server_ssl_context,
)
from ojmicroline_thermostat.gateway import server as gateway_server
from ojmicroline_thermostat.gateway import wg4_gateway as gateway_wg4
from ojmicroline_thermostat.gateway.protobuf import (
    MAX_FRAME,
    Message,
    encode_field,
    encode_frame,
    encode_varint,
)
from ojmicroline_thermostat.gateway.server import read_frame, tcp_state

from .gateway_fakes import (
    PROFILE,
    SCHEDULE,
    SERIAL,
    UPLOADED,
    Events,
    FakeThermostat,
    acknowledgement,
    login,
    until,
)
from .test_gateway_wg4 import TELEMETRY, WALL_STATE

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path


@pytest.fixture(name="gateway")
async def gateway_fixture(
    certificate: tuple[Path, Path], events: Events
) -> AsyncIterator[WG4Gateway]:
    """Run a gateway on a free port that answers one thermostat itself."""
    gateway = WG4Gateway(
        server_ssl_context(*certificate),
        mode=GatewayMode.LOCAL,
        profiles={SERIAL: PROFILE},
        listen_host="127.0.0.1",
        listen_port=0,
    )
    gateway.allowed_serials = {SERIAL}
    gateway.on_state = lambda serial, fields: events.append((serial, fields))
    await gateway.start()
    yield gateway
    await gateway.stop()


async def _connected(connect: Callable[[], Any]) -> FakeThermostat:
    """Connect a thermostat that has logged in and uploaded its state."""
    thermostat: FakeThermostat = await connect()
    await thermostat.login()
    await thermostat.upload()
    return thermostat


async def test_read_frame() -> None:
    """Frames are read from a stream one by one."""
    reader = asyncio.StreamReader()
    reader.feed_data(encode_frame(b"abc") + encode_frame(bytes(200)))
    assert await read_frame(reader) == b"abc"
    assert await read_frame(reader) == bytes(200)

    reader.feed_data(b"\x08\x05ab")
    reader.feed_eof()
    with pytest.raises(asyncio.IncompleteReadError):
        await read_frame(reader)


@pytest.mark.parametrize(
    ("data", "error"),
    [
        (b"\x09", "starts with 0x08, not with 0x09"),
        (b"\x08\xff\xff\xff\x7f", "at most 3 bytes"),
        (b"\x08" + encode_varint(MAX_FRAME + 1), "exceeds the limit"),
    ],
)
async def test_read_frame_rejects_bad_frame(data: bytes, error: str) -> None:
    """Data that is not a frame of a sane size is refused."""
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    with pytest.raises(ValueError, match=error):
        await read_frame(reader)


async def test_start_and_stop(certificate: tuple[Path, Path]) -> None:
    """The gateway listens between start() and stop(), and both can repeat."""
    gateway = WG4Gateway(
        server_ssl_context(*certificate),
        mode=GatewayMode.LOCAL,
        listen_host="127.0.0.1",
        listen_port=0,
    )
    assert gateway.port is None
    await gateway.stop()

    await gateway.start()
    port = gateway.port
    assert port
    await gateway.start()
    assert gateway.port == port

    await gateway.stop()
    assert gateway.port is None
    await gateway.stop()


async def test_start_fails_when_the_port_is_taken(
    gateway: WG4Gateway, certificate: tuple[Path, Path]
) -> None:
    """A second gateway on the same port reports why it cannot start."""
    second = WG4Gateway(
        server_ssl_context(*certificate),
        mode=GatewayMode.LOCAL,
        listen_host="127.0.0.1",
        listen_port=gateway.port,
    )
    with pytest.raises(OJMicrolineConnectionError, match="Unable to listen on port"):
        await second.start()
    assert second.port is None


def test_defaults() -> None:
    """Without further settings the gateway stands in for North America's."""
    gateway = WG4Gateway(
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER), mode=GatewayMode.LOCAL
    )
    assert gateway.mode is GatewayMode.LOCAL
    assert gateway.endpoint.host == "na-thermos.ojelectronics.com"
    assert gateway.endpoint.port == 9533
    assert not gateway.allowed_serials
    assert not gateway.profiles
    assert not gateway.connected
    assert not gateway.is_relayed(SERIAL)
    assert gateway.upstream_status is None


async def test_thermostat_cipher_suite(gateway: WG4Gateway) -> None:
    """The gateway accepts the kind of TLS connection the thermostat makes.

    The thermostat speaks TLS 1.2 with RSA key exchange, which current defaults
    put last or leave out.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    try:
        context.set_ciphers("AES256-SHA256")
    except ssl.SSLError:
        pytest.skip("This OpenSSL build cannot connect the way the thermostat does")

    thermostat = await FakeThermostat.connect(gateway.port, context)
    try:
        assert thermostat.cipher == "AES256-SHA256"
        assert (await thermostat.login()).varint(3) == 1
    finally:
        await thermostat.close()


async def test_session(
    gateway: WG4Gateway, events: Events, connect: Callable[[], Any]
) -> None:
    """The gateway answers a thermostat the way OJ's gateway does."""
    thermostat: FakeThermostat = await connect()

    # The login is acknowledged with the server time.
    answer = await thermostat.login()
    assert answer.numbers == {1, 3, 4, 12}
    assert answer.varint(1) == 29
    assert answer.varint(3) == 1
    assert gateway.connected == {SERIAL}
    assert not gateway.is_relayed(SERIAL)
    assert events == [(SERIAL, {"Online": True})]

    # Each upload is acknowledged, followed by the answer from the profile:
    # support after the schedule, configuration after the state.
    assert await thermostat.upload() == [
        encode_frame(acknowledgement(30)),
        PROFILE.support,
        encode_frame(acknowledgement(31)),
        PROFILE.configuration,
    ]
    assert events[-1] == (SERIAL, UPLOADED)

    # A heartbeat and a change at the wall are acknowledged and passed on.
    await thermostat.send(TELEMETRY)
    assert (await thermostat.receive()).varint(1) == 403
    assert events[-1] == (SERIAL, {"Temperature": 2033, "Heating": False})

    await thermostat.send(WALL_STATE)
    assert (await thermostat.receive()).varint(1) == 406
    assert events[-1] == (
        SERIAL,
        {
            "RegulationMode": 2,
            "ComfortTemperature": 2166,
            "ManualTemperature": 1444,
            "SetPointTemp": 2166,
        },
    )


async def test_history(gateway: WG4Gateway, connect: Callable[[], Any]) -> None:
    """The gateway keeps what happened, for a report of a real thermostat."""
    thermostat = await _connected(connect)
    await thermostat.send(TELEMETRY)
    await thermostat.receive()
    task = asyncio.create_task(gateway.set_mode(SERIAL, 3, 2250))
    await thermostat.receive()
    await thermostat.send(acknowledgement(101))
    await task

    history = gateway.history
    events = "\n".join(history)
    assert f"The gateway listens on port {gateway.port} (local mode)" in events
    assert "Connection from 127.0.0.1, secured with TLSv1." in events
    assert f"Thermostat {SERIAL} logged in; it is answered locally" in events
    assert (
        f"Thermostat (thermostat {SERIAL}): seq=403 fields=1,5,15 "
        "floor=20.33C/68.6F heating=0"
    ) in events
    assert f"Sending command 101 to thermostat {SERIAL}" in events
    assert f"Thermostat {SERIAL} acknowledged command 101" in events
    # Every line has its time, and a login never shows what is in it.
    assert all(line[4] == "-" and line[13] == ":" for line in history)
    assert "user@example.com" not in events


async def test_history_is_bounded(
    gateway: WG4Gateway, connect: Callable[[], Any]
) -> None:
    """The history keeps the latest events, not all of them."""
    thermostat: FakeThermostat = await connect()
    await thermostat.login()
    for _ in range(320):
        await thermostat.send(TELEMETRY)
        await thermostat.receive()

    assert len(gateway.history) == 300
    assert "logged in" not in "\n".join(gateway.history)


async def test_failed_handshake_is_reported(gateway: WG4Gateway) -> None:
    """A thermostat that does not get through TLS leaves a trace.

    This is what shows when a thermostat refuses the gateway's certificate.
    """
    # A client that checks the certificate, which no authority vouches for.
    with pytest.raises(ssl.SSLCertVerificationError):
        await FakeThermostat.connect(gateway.port, ssl.create_default_context())
    # A client that does not speak TLS at all.
    reader, writer = await asyncio.open_connection("127.0.0.1", gateway.port)
    writer.write(b"this is not TLS\r\n\r\n")
    await writer.drain()
    async with asyncio.timeout(2):
        await reader.read()
    writer.close()

    await until(lambda: "\n".join(gateway.history).count("TLS handshake") == 2)
    assert all(
        "The TLS handshake with 127.0.0.1 failed: " in line
        for line in gateway.history
        if "TLS handshake" in line
    )
    assert not gateway.connected


async def test_messages_without_an_answer(
    events: Events, connect: Callable[[], Any]
) -> None:
    """Only a message with a sequence number is acknowledged."""
    thermostat: FakeThermostat = await connect()
    await thermostat.login()
    seen = len(events)

    # Without a sequence number, and the thermostat's own acknowledgement of
    # a message it got: neither is answered, and neither says anything.
    await thermostat.send(encode_field(20, 1))
    await thermostat.send(acknowledgement(0))
    await thermostat.send(acknowledgement(999))
    await thermostat.send(encode_field(3, 1))
    # An unknown message with a sequence number is acknowledged.
    await thermostat.send(encode_field(1, 55) + encode_field(20, 1))

    answer = await thermostat.receive()
    assert answer.varint(1) == 55
    assert answer.varint(3) == 1
    assert len(events) == seen


async def test_disconnect(
    gateway: WG4Gateway, events: Events, connect: Callable[[], Any]
) -> None:
    """A thermostat that hangs up is reported offline."""
    thermostat: FakeThermostat = await connect()
    await thermostat.login()
    await thermostat.close()

    await until(lambda: not gateway.connected)
    assert events == [(SERIAL, {"Online": True}), (SERIAL, {"Online": False})]


async def test_reconnect_replaces_the_old_connection(
    gateway: WG4Gateway, events: Events, connect: Callable[[], Any]
) -> None:
    """A thermostat that connects again stays online on its new connection."""
    first: FakeThermostat = await connect()
    await first.login()
    second: FakeThermostat = await connect()
    await second.login()

    assert await first.was_closed()
    await second.send(TELEMETRY)
    assert (await second.receive()).varint(1) == 403
    assert gateway.connected == {SERIAL}
    # The thermostat never went offline in between.
    assert (SERIAL, {"Online": False}) not in events


async def test_stop_disconnects_thermostats(
    gateway: WG4Gateway, events: Events, connect: Callable[[], Any]
) -> None:
    """Stopping the gateway hangs up on thermostats, logged in or not."""
    waiting: FakeThermostat = await connect()
    thermostat: FakeThermostat = await connect()
    await thermostat.login()

    await gateway.stop()

    assert await thermostat.was_closed()
    assert await waiting.was_closed()
    assert not gateway.connected
    assert events[-1] == (SERIAL, {"Online": False})


@pytest.mark.parametrize(
    "first",
    [
        TELEMETRY,
        # A login without a serial number.
        encode_field(1, 9) + encode_field(6, b""),
    ],
)
async def test_first_message_must_be_a_login(
    first: bytes, gateway: WG4Gateway, events: Events, connect: Callable[[], Any]
) -> None:
    """Nothing is answered before a thermostat said who it is."""
    thermostat: FakeThermostat = await connect()
    await thermostat.send(first)

    assert await thermostat.was_closed()
    assert not gateway.connected
    assert not events


async def test_unknown_thermostat_is_refused(
    gateway: WG4Gateway, events: Events, connect: Callable[[], Any]
) -> None:
    """A thermostat that is not allowed, or has no profile, gets no answer."""
    stranger: FakeThermostat = await connect()
    await stranger.send(login("7654321"))
    assert await stranger.was_closed()

    gateway.allowed_serials.add("7654321")
    without_profile: FakeThermostat = await connect()
    await without_profile.send(login("7654321"))
    assert await without_profile.was_closed()

    assert not gateway.connected
    assert not events


async def test_second_login_is_refused(
    gateway: WG4Gateway, connect: Callable[[], Any]
) -> None:
    """A connection belongs to one thermostat; it cannot log in again."""
    thermostat: FakeThermostat = await connect()
    await thermostat.login()
    await thermostat.send(login())

    assert await thermostat.was_closed()
    await until(lambda: not gateway.connected)


@pytest.mark.parametrize(
    "data",
    [
        b"\x09",
        b"\x08\xff\xff\xff\x7f",
        b"\x08" + encode_varint(MAX_FRAME + 1),
        encode_frame(b"\x00"),
        # A state that is not a message.
        encode_frame(encode_field(1, 5) + encode_field(9, b"\x00")),
    ],
)
async def test_malformed_input_closes_the_connection(
    data: bytes,
    gateway: WG4Gateway,
    connect: Callable[[], Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A thermostat that sends nonsense is dropped, and the gateway goes on."""
    caplog.set_level(logging.DEBUG)
    thermostat: FakeThermostat = await connect()
    await thermostat.login()
    await thermostat.send_raw(data)

    assert await thermostat.was_closed()
    await until(lambda: not gateway.connected)
    assert "malformed message" in caplog.text

    again: FakeThermostat = await connect()
    await again.login()
    assert gateway.connected == {SERIAL}


async def test_unexpected_error_closes_the_connection(
    gateway: WG4Gateway,
    connect: Callable[[], Any],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bug in handling one thermostat does not take the gateway down."""

    def broken(_message: Message) -> dict[str, Any]:
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(gateway_wg4, "decode_telemetry", broken)
    thermostat: FakeThermostat = await connect()
    await thermostat.login()
    await thermostat.send(TELEMETRY)

    assert await thermostat.was_closed()
    await until(lambda: not gateway.connected)
    assert "Unexpected error in the connection of a thermostat" in caplog.text


async def test_login_has_to_come_in_time(
    connect: Callable[[], Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A connection that never logs in is closed."""
    monkeypatch.setattr(gateway_server, "LOGIN_TIMEOUT", 0.05)
    thermostat: FakeThermostat = await connect()

    assert await thermostat.was_closed()


async def test_quiet_thermostat_is_disconnected(
    gateway: WG4Gateway,
    events: Events,
    connect: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without heartbeats the link is dead, and the thermostat is offline."""
    monkeypatch.setattr(gateway_server, "IDLE_TIMEOUT", 0.05)
    thermostat: FakeThermostat = await connect()
    await thermostat.login()

    assert await thermostat.was_closed()
    await until(lambda: not gateway.connected)
    assert events[-1] == (SERIAL, {"Online": False})


async def test_listener_is_optional_and_may_fail(
    gateway: WG4Gateway,
    connect: Callable[[], Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The gateway keeps serving without a listener, and with a broken one."""

    def broken(_serial: str, _fields: dict[str, Any]) -> None:
        msg = "boom"
        raise RuntimeError(msg)

    gateway.on_state = None
    thermostat: FakeThermostat = await connect()
    await thermostat.login()

    gateway.on_state = broken
    await thermostat.send(TELEMETRY)
    assert (await thermostat.receive()).varint(1) == 403
    assert "Error in a listener of the gateway" in caplog.text


async def test_set_mode(
    gateway: WG4Gateway, events: Events, connect: Callable[[], Any]
) -> None:
    """A command is the thermostat's own state with the mode and setpoint set."""
    thermostat = await _connected(connect)

    task = asyncio.create_task(gateway.set_mode(SERIAL, 3, 2250))
    sent = await thermostat.receive()
    assert sent.numbers == {1, 5, 8, 9}
    assert sent.varint(1) == 101
    assert sent.data(8) == SCHEDULE
    state = sent.message(9)
    assert state is not None
    assert state.varint(2) == 3
    # 22.5 °C is 4500 on the wire, in the field of the permanent hold. The
    # temporary hold keeps its setpoint.
    assert state.message(5) == Message.parse(encode_field(1, 4500))
    assert state.message(4) == Message.parse(encode_field(1, 3332))
    assert not task.done()

    await thermostat.send(acknowledgement(101))
    await task
    assert events[-1] == (
        SERIAL,
        {
            "RegulationMode": 3,
            "ComfortTemperature": 1666,
            "ManualTemperature": 2250,
            "SetPointTemp": 2250,
        },
    )

    # The next command starts from the state the thermostat took over.
    task = asyncio.create_task(gateway.set_mode(SERIAL, 1))
    sent = await thermostat.receive()
    assert sent.varint(1) == 102
    state = sent.message(9)
    assert state is not None
    assert state.varint(2) == 1
    assert state.message(5) == Message.parse(encode_field(1, 4500))
    await thermostat.send(acknowledgement(102))
    await task
    assert events[-1][1]["RegulationMode"] == 1


async def test_set_mode_one_at_a_time(
    gateway: WG4Gateway, connect: Callable[[], Any]
) -> None:
    """A second command waits until the thermostat answered the first."""
    thermostat = await _connected(connect)

    first = asyncio.create_task(gateway.set_mode(SERIAL, 3, 2250))
    second = asyncio.create_task(gateway.set_mode(SERIAL, 2, 2000))
    assert (await thermostat.receive()).varint(1) == 101
    await thermostat.send(acknowledgement(101))
    await first

    # The second command builds on the first: the permanent hold keeps 22.5 °C.
    sent = await thermostat.receive()
    assert sent.varint(1) == 102
    state = sent.message(9)
    assert state is not None
    assert state.varint(2) == 2
    assert state.message(4) == Message.parse(encode_field(1, 4000))
    assert state.message(5) == Message.parse(encode_field(1, 4500))
    await thermostat.send(acknowledgement(102))
    await second


async def test_set_mode_needs_a_connected_thermostat(
    gateway: WG4Gateway, connect: Callable[[], Any]
) -> None:
    """A command for a thermostat that cannot take it fails right away."""
    with pytest.raises(OJMicrolineConnectionError, match="is not connected"):
        await gateway.set_mode(SERIAL, 3, 2250)

    # Connected, but the state to build the command from is not there yet.
    thermostat: FakeThermostat = await connect()
    await thermostat.login()
    with pytest.raises(OJMicrolineConnectionError, match="not reported its state"):
        await gateway.set_mode(SERIAL, 3, 2250)


async def test_set_mode_without_acknowledgement(
    gateway: WG4Gateway,
    events: Events,
    connect: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A command the thermostat does not confirm in time fails."""
    monkeypatch.setattr(gateway_server, "ACKNOWLEDGE_TIMEOUT", 0.05)
    thermostat = await _connected(connect)
    seen = len(events)

    with pytest.raises(OJMicrolineTimeoutError, match="did not acknowledge"):
        await gateway.set_mode(SERIAL, 3, 2250)
    assert gateway.history[-1].endswith(
        f"Thermostat {SERIAL} did not answer command 101 within 0.05 seconds"
    )
    assert (await thermostat.receive()).varint(1) == 101
    assert len(events) == seen

    # Until it answers, the next command starts from the state the thermostat
    # reported: the permanent hold still has 22.22 °C.
    task = asyncio.create_task(gateway.set_mode(SERIAL, 1))
    sent = await thermostat.receive()
    assert sent.varint(1) == 102
    state = sent.message(9)
    assert state is not None
    assert state.message(5) == Message.parse(encode_field(1, 4444))
    await thermostat.send(acknowledgement(102, accepted=0))
    with pytest.raises(OJMicrolineError, match="refused"):
        await task


async def test_late_acknowledgement_still_counts(
    gateway: WG4Gateway,
    events: Events,
    connect: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A thermostat that confirms after the wait ended has still changed."""
    monkeypatch.setattr(gateway_server, "ACKNOWLEDGE_TIMEOUT", 0.05)
    thermostat = await _connected(connect)
    seen = len(events)

    with pytest.raises(OJMicrolineTimeoutError, match="did not acknowledge"):
        await gateway.set_mode(SERIAL, 3, 2250)
    await thermostat.receive()
    await asyncio.sleep(0.05)
    await thermostat.send(acknowledgement(101))

    # The listener hears what the thermostat took over after all.
    await until(lambda: len(events) == seen + 1)
    assert events[-1] == (
        SERIAL,
        {
            "RegulationMode": 3,
            "ComfortTemperature": 1666,
            "ManualTemperature": 2250,
            "SetPointTemp": 2250,
        },
    )
    assert "acknowledged command 101 after" in gateway.history[-1]
    assert gateway.history[-1].endswith("after the wait for it had ended")

    # The next command starts from it: the permanent hold has 22.5 °C now.
    monkeypatch.setattr(gateway_server, "ACKNOWLEDGE_TIMEOUT", 2)
    task = asyncio.create_task(gateway.set_mode(SERIAL, 1))
    sent = await thermostat.receive()
    state = sent.message(9)
    assert state is not None
    assert state.message(5) == Message.parse(encode_field(1, 4500))
    await thermostat.send(acknowledgement(102))
    await task


async def test_timeout_tells_what_the_network_did(
    gateway: WG4Gateway,
    connect: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The state of the connection is part of the event, where it is known."""
    monkeypatch.setattr(gateway_server, "ACKNOWLEDGE_TIMEOUT", 0.05)
    monkeypatch.setattr(gateway_server, "tcp_state", lambda _writer: "TCP: stuck")
    await _connected(connect)

    with pytest.raises(OJMicrolineTimeoutError):
        await gateway.set_mode(SERIAL, 3, 2250)
    assert gateway.history[-1].endswith("within 0.05 seconds (TCP: stuck)")


def _tcp_writer(answer: bytes | Exception | None) -> MagicMock:
    """Create a writer whose socket answers a request for its TCP state."""
    writer = MagicMock(spec=asyncio.StreamWriter)
    if answer is None:
        writer.get_extra_info.return_value = None
        return writer
    connection = MagicMock()
    if isinstance(answer, Exception):
        connection.getsockopt.side_effect = answer
    else:
        connection.getsockopt.return_value = answer
    writer.get_extra_info.return_value = connection
    return writer


def test_tcp_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Linux tells how much of what was sent has not arrived."""
    monkeypatch.setattr(socket, "TCP_INFO", 11, raising=False)
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    counters = [0] * 24
    counters[0] = 12_800_000  # The retransmission timer, in microseconds.
    counters[2] = 1460  # The segment size.
    counters[4] = 1  # Segments that are not acknowledged.
    counters[12] = 13_200  # Milliseconds since the last acknowledgement.
    counters[15] = 45_000  # The round trip, in microseconds.
    counters[23] = 9  # Retransmissions in total.
    raw = struct.pack("=8B24I", 1, 0, 6, 0, 6, 0, 0, 0, *counters)

    assert tcp_state(_tcp_writer(raw)) == (
        "TCP: 1 segments unacknowledged, the oldest sent again 6 times, "
        "9 retransmissions in total, last acknowledgement 13.2 s ago, "
        "retransmission timer 12.8 s, round trip 45 ms, segment size 1460"
    )
    # A system that answers with less, or refuses, tells nothing.
    assert tcp_state(_tcp_writer(raw[:20])) == ""
    assert tcp_state(_tcp_writer(OSError("not supported"))) == ""
    assert tcp_state(_tcp_writer(None)) == ""


def test_tcp_state_is_only_read_on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    """Other systems lay the same request out differently, or lack it."""
    writer = _tcp_writer(bytes(104))
    monkeypatch.setattr(socket, "TCP_INFO", 11, raising=False)
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    assert tcp_state(writer) == ""

    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.delattr(socket, "TCP_INFO")
    assert tcp_state(writer) == ""
    writer.get_extra_info.return_value.getsockopt.assert_not_called()


async def test_set_mode_refused(
    gateway: WG4Gateway, events: Events, connect: Callable[[], Any]
) -> None:
    """An answer that does not confirm the command is an error."""
    thermostat = await _connected(connect)
    seen = len(events)

    task = asyncio.create_task(gateway.set_mode(SERIAL, 3, 2250))
    await thermostat.receive()
    await thermostat.send(acknowledgement(101, accepted=0))

    with pytest.raises(OJMicrolineError, match="refused the command"):
        await task
    assert f"Thermostat {SERIAL} refused command 101" in gateway.history[-1]
    assert len(events) == seen


async def test_closing_a_link_leaves_finished_commands_alone() -> None:
    """Only commands that still wait are told that the thermostat is gone.

    A command can time out in the same moment its thermostat disconnects; its
    wait has ended by then, and failing it once more would be an error.
    """
    loop = asyncio.get_running_loop()
    finished: asyncio.Future[bool] = loop.create_future()
    finished.set_result(True)
    waiting: asyncio.Future[bool] = loop.create_future()
    writer = MagicMock(spec=asyncio.StreamWriter)
    # pylint: disable=protected-access
    link = gateway_server._Link(SERIAL, writer)
    link.unanswered.extend(
        [
            gateway_server._Sent(101, lambda: None, finished),
            gateway_server._Sent(102, lambda: None, waiting),
            # A message of OJ's gateway, which nobody here waits for.
            gateway_server._Sent(7),
        ]
    )
    # pylint: enable=protected-access

    WG4Gateway._close(link)  # pylint: disable=protected-access

    assert link.closed
    assert finished.result() is True
    with pytest.raises(ConnectionResetError, match="disconnected"):
        waiting.result()
    writer.close.assert_called_once_with()


async def test_set_mode_when_the_thermostat_disconnects(
    gateway: WG4Gateway, connect: Callable[[], Any]
) -> None:
    """Commands do not wait for a thermostat that hung up."""
    thermostat = await _connected(connect)

    first = asyncio.create_task(gateway.set_mode(SERIAL, 3, 2250))
    second = asyncio.create_task(gateway.set_mode(SERIAL, 1))
    await thermostat.receive()
    await thermostat.close()

    with pytest.raises(OJMicrolineConnectionError, match="before it acknowledged"):
        await first
    # The second command waited for the first, and finds the connection gone.
    with pytest.raises(OJMicrolineConnectionError, match="disconnected"):
        await second
    await until(lambda: not gateway.connected)
