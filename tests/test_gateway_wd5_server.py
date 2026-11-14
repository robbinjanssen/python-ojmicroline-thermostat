"""Test the gateway that WD5-series thermostats connect to.

A fake thermostat talks to a real gateway over TLS on the loopback interface,
alone and with a fake OJ gateway behind it. The messages have the shape of
those of an OWD5 thermostat; test_gateway_wd5.py holds the captured ones.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any

import pytest

from ojmicroline_thermostat import OJMicrolineConnectionError
from ojmicroline_thermostat.const import (
    REGULATION_COMFORT,
    REGULATION_MANUAL,
    REGULATION_SCHEDULE,
)
from ojmicroline_thermostat.gateway import (
    WD5_R1099,
    GatewayEndpoint,
    GatewayMode,
    WD5Gateway,
    server_ssl_context,
)
from ojmicroline_thermostat.gateway import wd5_gateway as gateway_wd5
from ojmicroline_thermostat.gateway.protobuf import Message, encode_field, encode_frame
from ojmicroline_thermostat.gateway.wd5 import (
    changed_state,
    command,
    decode_state,
    default_state,
)

from .gateway_fakes import Events, FakeCloud, FakeThermostat, acknowledgement, until

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path

SERIAL = "1021410"

# What a thermostat sends after it connected. Setpoints are in 1/200 °C, and
# the state holds only what matters in the schedule mode.
INFO = (
    encode_field(1, 2)
    + encode_field(4, 1)
    + encode_field(7, encode_field(2, b"Bad") + encode_field(3, 3600))
)
STATE = (
    encode_field(1, 3)
    + encode_field(4, 1)
    + encode_field(
        9,
        encode_field(1, 847837681)
        + encode_field(2, REGULATION_SCHEDULE)
        + encode_field(4, encode_field(1, 4300)),
    )
)
HEARTBEAT = (
    encode_field(1, 4)
    + encode_field(4, 1)
    + encode_field(
        6, encode_field(2, 2150) + encode_field(3, 2300) + encode_field(4, 1)
    )
)
# What the cloud API knows of the thermostat, in its own names and units.
KNOWN = {
    "ComfortSetpoint": 2000,
    "ManualModeSetpoint": 2100,
    "FrostProtectionTemperature": 500,
    "VacationEnabled": True,
    "VacationBeginDay": "2026-12-20T00:00:00",
    "VacationEndDay": "2027-01-03T00:00:00",
    "VacationTemperature": 1200,
}


def login(serial: str = SERIAL) -> bytes:
    """Build a login like the thermostat's: serial, type, firmware, clock."""
    clock = encode_field(1, 9) + encode_field(2, 30) + encode_field(3, 0)
    about = (
        encode_field(1, serial.encode())
        + encode_field(2, 3)
        + encode_field(3, b"1013W220")
        + encode_field(5, clock)
    )
    return encode_field(1, 1) + encode_field(4, 1) + encode_field(5, about)


def _reported(state: bytes) -> dict[str, Any]:
    """Return what the gateway reports for a complete state."""
    return decode_state(Message.parse(encode_field(9, state)))


@pytest.fixture(name="gateway")
async def gateway_fixture(
    certificate: tuple[Path, Path], events: Events
) -> AsyncIterator[WD5Gateway]:
    """Run a gateway on a free port that answers one thermostat itself."""
    gateway = WD5Gateway(
        server_ssl_context(*certificate),
        mode=GatewayMode.LOCAL,
        listen_host="127.0.0.1",
        listen_port=0,
    )
    gateway.allowed_serials = {SERIAL}
    gateway.on_state = lambda serial, fields: events.append((serial, fields))
    await gateway.start()
    yield gateway
    await gateway.stop()


async def _uploaded(connect: Callable[[], Any]) -> FakeThermostat:
    """Connect a thermostat that has logged in and uploaded what it holds."""
    thermostat: FakeThermostat = await connect()
    await thermostat.send(login())
    await thermostat.receive()
    await thermostat.receive()
    for message in (INFO, STATE):
        await thermostat.send(message)
        await thermostat.receive()
    return thermostat


def test_default_endpoint(certificate: tuple[Path, Path]) -> None:
    """Without an endpoint the gateway stands in for that of the SWATT app."""
    gateway = WD5Gateway(server_ssl_context(*certificate), mode=GatewayMode.LOCAL)
    assert gateway.endpoint is WD5_R1099
    assert gateway.endpoint.port == 9513


async def test_local_session(
    gateway: WD5Gateway, events: Events, connect: Callable[[], Any]
) -> None:
    """The gateway answers a thermostat by itself, without anything recorded."""
    thermostat: FakeThermostat = await connect()
    await thermostat.send(login())

    # The answer to the login tells the time, and the full upload is asked for.
    answer = await thermostat.receive()
    assert (answer.varint(1), answer.varint(3), answer.varint(4)) == (1, 1, 1)
    assert 12 in answer
    request = await thermostat.receive()
    assert (request.varint(1), request.varint(4)) == (0, 1)
    assert request.message(10) is not None
    assert gateway.connected == {SERIAL}
    assert not gateway.is_relayed(SERIAL)
    assert events == [(SERIAL, {"Online": True})]

    # The thermostat acknowledges the request, which needs no answer, and
    # uploads. Every message that wants an answer gets one with the time.
    await thermostat.send(acknowledgement(0))
    for message, sequence in ((INFO, 2), (STATE, 3), (HEARTBEAT, 4)):
        await thermostat.send(message)
        answer = await thermostat.receive()
        assert (answer.varint(1), answer.varint(3)) == (sequence, 1)
        assert 12 in answer
    assert events[1:] == [
        (SERIAL, {"ThermostatName": "Bad", "TimeZone": 3600}),
        (SERIAL, {"RegulationMode": REGULATION_SCHEDULE, "ManualModeSetpoint": 2150}),
        (SERIAL, {"RoomTemperature": 2150, "FloorTemperature": 2300, "Heating": True}),
    ]

    # A message that wants no answer gets none, and one that says nothing
    # about the thermostat is not reported.
    await thermostat.send(encode_field(1, 5) + encode_field(12, encode_field(1, 7)))
    await thermostat.send(HEARTBEAT)
    assert (await thermostat.receive()).varint(1) == 4
    assert len(events) == 5

    history = "\n".join(gateway.history)
    assert f"Thermostat {SERIAL} logged in; it is answered locally" in history
    assert f"Thermostat (thermostat {SERIAL}): seq=4 fields=1,4,6" in history


async def test_other_logins_are_refused(connect: Callable[[], Any]) -> None:
    """Only the thermostats of the account are served, and only WD5 logins."""
    stranger: FakeThermostat = await connect()
    await stranger.send(login("9999999"))
    assert await stranger.was_closed()

    # The login of another series, and a login without a serial number.
    for message in (
        encode_field(1, 1) + encode_field(6, encode_field(2, SERIAL.encode())),
        encode_field(1, 1) + encode_field(5, encode_field(2, 3)),
    ):
        other: FakeThermostat = await connect()
        await other.send(message)
        assert await other.was_closed()


async def test_set_mode_needs_the_upload(
    gateway: WD5Gateway, connect: Callable[[], Any]
) -> None:
    """A command for a thermostat that cannot take it fails right away."""
    with pytest.raises(OJMicrolineConnectionError, match="is not connected"):
        await gateway.set_mode(SERIAL, REGULATION_MANUAL, 2200)

    thermostat: FakeThermostat = await connect()
    await thermostat.send(login())
    await thermostat.receive()
    await thermostat.receive()
    with pytest.raises(OJMicrolineConnectionError, match="not reported its state"):
        await gateway.set_mode(SERIAL, REGULATION_MANUAL, 2200)

    # The heartbeat is not the upload, the device info is.
    await thermostat.send(HEARTBEAT)
    await thermostat.receive()
    with pytest.raises(OJMicrolineConnectionError, match="not reported its state"):
        await gateway.set_mode(SERIAL, REGULATION_MANUAL, 2200)
    await thermostat.send(INFO)
    await thermostat.receive()
    task = asyncio.create_task(gateway.set_mode(SERIAL, REGULATION_MANUAL, 2200))
    sent = await thermostat.receive()
    await thermostat.send(acknowledgement(sent.varint(1) or 0))
    await task


async def test_first_command_is_built_from_what_is_known(
    gateway: WD5Gateway, events: Events, connect: Callable[[], Any]
) -> None:
    """A command is a complete state; what is not known must not be erased."""
    thermostat = await _uploaded(connect)
    end = datetime(2026, 11, 14, 18, 30, 0)  # noqa: DTZ001 - thermostat time

    task = asyncio.create_task(
        gateway.set_mode(SERIAL, REGULATION_COMFORT, 2450, end=end, known=KNOWN)
    )
    sent = await thermostat.receive()
    assert (sent.varint(1), sent.varint(4)) == (101, 1)
    state = sent.data(9)
    assert state is not None
    assert _reported(state) == {
        "RegulationMode": REGULATION_COMFORT,
        "ComfortSetpoint": 2450,
        "ComfortEndTime": "2026-11-14T18:30:00",
        # What the thermostat reported itself goes before what the cloud says.
        "ManualModeSetpoint": 2150,
        "VacationEnabled": True,
        "VacationBeginDay": "2026-12-20T00:00:00",
        "VacationEndDay": "2027-01-03T00:00:00",
        "VacationTemperature": 1200,
        "BoostEndTime": "1970-01-01T00:00:00",
        "FrostProtectionTemperature": 500,
    }
    seen = len(events)
    await thermostat.send(acknowledgement(101))
    await task
    assert len(events) == seen + 1
    assert events[-1] == (SERIAL, _reported(state))

    # The next command starts from that state: the comfort hold is kept.
    task = asyncio.create_task(gateway.set_mode(SERIAL, REGULATION_MANUAL, 2200))
    sent = await thermostat.receive()
    assert sent.varint(1) == 102
    following = sent.data(9)
    assert following is not None
    assert _reported(following) == {
        **_reported(state),
        "RegulationMode": REGULATION_MANUAL,
        "ManualModeSetpoint": 2200,
    }
    await thermostat.send(acknowledgement(102))
    await task


async def test_set_mode_when_the_thermostat_disconnects(
    gateway: WD5Gateway, connect: Callable[[], Any]
) -> None:
    """Commands do not wait for a thermostat that hung up."""
    thermostat = await _uploaded(connect)

    first = asyncio.create_task(gateway.set_mode(SERIAL, REGULATION_MANUAL, 2250))
    second = asyncio.create_task(gateway.set_mode(SERIAL, REGULATION_SCHEDULE))
    await thermostat.receive()
    await thermostat.close()

    with pytest.raises(OJMicrolineConnectionError, match="before it acknowledged"):
        await first
    with pytest.raises(OJMicrolineConnectionError, match="disconnected"):
        await second
    await until(lambda: not gateway.connected)


@pytest.mark.parametrize(
    ("known", "expected"),
    [
        # Nothing known: the state has no vacation, and default setpoints.
        ({}, default_state()),
        # A vacation whose days are not set, or not days at all.
        (
            {
                "VacationEnabled": False,
                "VacationBeginDay": "1970-01-01T00:00:00",
                "VacationEndDay": 20270103,
            },
            default_state(),
        ),
        ({"VacationEnabled": 1, "VacationBeginDay": "soon"}, None),
    ],
)
def test_base_state(known: dict[str, Any], expected: bytes | None) -> None:
    """The base of a first command holds the vacation the cloud API knows."""
    # pylint: disable-next=protected-access
    state = gateway_wd5._base_state(known)
    reported = _reported(state)
    if expected is None:
        assert reported["VacationEnabled"] is True
        assert reported["VacationBeginDay"] == "1970-01-01T00:00:00"
    else:
        # Only the time of the change differs between two builds.
        assert reported == _reported(expected)


# --- Relayed -------------------------------------------------------------------


@pytest.fixture(name="relay")
async def relay_fixture(
    certificate: tuple[Path, Path], cloud: FakeCloud, events: Events
) -> AsyncIterator[WD5Gateway]:
    """Run a gateway that relays one thermostat to a fake OJ gateway."""
    gateway = WD5Gateway(
        server_ssl_context(*certificate),
        mode=GatewayMode.RELAY,
        endpoint=GatewayEndpoint(
            host=WD5_R1099.host,
            port=cloud.port,
            upstream_ip="127.0.0.1",
            pin_sha256=cloud.fingerprint,
        ),
        listen_host="127.0.0.1",
        listen_port=0,
    )
    gateway.allowed_serials = {SERIAL}
    gateway.on_state = lambda serial, fields: events.append((serial, fields))
    await gateway.start()
    yield gateway
    await gateway.stop()


async def test_relayed_session(
    relay: WD5Gateway, cloud: FakeCloud, events: Events
) -> None:
    """A relayed thermostat talks to OJ's gateway, and is decoded on the way."""
    thermostat = await FakeThermostat.connect(relay.port)
    try:
        await thermostat.send(login())
        # The fake OJ gateway answers every message with its number.
        assert (await thermostat.receive()).varint(1) == 1
        assert relay.is_relayed(SERIAL)
        for message in (INFO, STATE):
            await thermostat.send(message)
            await thermostat.receive()
        assert [message.varint(1) for message in cloud.received] == [1, 2, 3]
        assert events[-1][1]["ManualModeSetpoint"] == 2150

        # A change in the vendor's app is a complete state. It is reported,
        # and its answer goes back to OJ's gateway.
        from_the_app = command(
            7, changed_state(default_state(KNOWN), REGULATION_MANUAL, 2300)
        )
        await cloud.send(from_the_app)
        assert await thermostat.receive_frame() == encode_frame(from_the_app)
        await until(lambda: events[-1][1].get("ManualModeSetpoint") == 2300)
        await thermostat.send(acknowledgement(7))
        await until(lambda: len(cloud.received) == 4)

        # The next command of the gateway starts from that state, and OJ's
        # gateway sees neither the command nor its answer.
        task = asyncio.create_task(relay.set_mode(SERIAL, REGULATION_SCHEDULE))
        sent = await thermostat.receive()
        state = sent.data(9)
        assert state is not None
        assert _reported(state)["RegulationMode"] == REGULATION_SCHEDULE
        assert _reported(state)["ManualModeSetpoint"] == 2300
        assert _reported(state)["ComfortSetpoint"] == 2000
        await thermostat.send(acknowledgement(101))
        await task
        assert len(cloud.received) == 4
    finally:
        await thermostat.close()


async def test_malformed_state_of_ojs_gateway_is_not_a_base(
    relay: WD5Gateway, cloud: FakeCloud
) -> None:
    """A command that cannot be decoded is passed on, and nothing more."""
    thermostat = await FakeThermostat.connect(relay.port)
    try:
        await thermostat.send(login())
        await thermostat.receive()
        await thermostat.send(STATE)
        await thermostat.receive()

        odd = encode_field(1, 8) + encode_field(4, 1) + encode_field(9, b"\x00")
        await cloud.send(odd)
        assert await thermostat.receive_frame() == encode_frame(odd)
        await until(lambda: "not understood" in relay.history[-1])

        # The next command starts from what the thermostat reported.
        task = asyncio.create_task(relay.set_mode(SERIAL, REGULATION_MANUAL))
        sent = await thermostat.receive()
        state = sent.data(9)
        assert state is not None
        assert _reported(state)["ManualModeSetpoint"] == 2150
        await thermostat.send(acknowledgement(sent.varint(1) or 0))
        await task
    finally:
        await thermostat.close()
