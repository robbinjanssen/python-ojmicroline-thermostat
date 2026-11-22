"""Test how the gateway relays thermostats to OJ's gateway.

A fake thermostat connects to a real gateway, which passes the connection on
to a fake OJ gateway. Everything runs over TLS on the loopback interface.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest

from ojmicroline_thermostat.exceptions import OJMicrolineTimeoutError
from ojmicroline_thermostat.gateway import (
    WG4_NORTH_AMERICA,
    DeviceProfile,
    GatewayEndpoint,
    GatewayMode,
    UpstreamStatus,
    WG4Gateway,
    server_ssl_context,
)
from ojmicroline_thermostat.gateway import server as gateway_server
from ojmicroline_thermostat.gateway.protobuf import Message, encode_field, encode_frame
from ojmicroline_thermostat.gateway.wg4 import changed_state, command

from .gateway_fakes import (
    CLOUD_PROFILE,
    PROFILE,
    SCHEDULE,
    SCHEDULE_UPLOAD,
    SERIAL,
    STATE,
    STATE_UPLOAD,
    UPLOADED,
    Events,
    FakeCloud,
    FakeThermostat,
    acknowledgement,
    login,
    until,
)
from .test_gateway_wg4 import TELEMETRY

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path

Recorded = list[tuple[str, DeviceProfile]]

# The answers to the uploads, as OJ's gateway and as a stored profile give them.
FROM_THE_CLOUD = [
    encode_frame(acknowledgement(30)),
    CLOUD_PROFILE.support,
    encode_frame(acknowledgement(31)),
    CLOUD_PROFILE.configuration,
]
FROM_THE_PROFILE = [
    encode_frame(acknowledgement(30)),
    PROFILE.support,
    encode_frame(acknowledgement(31)),
    PROFILE.configuration,
]


@pytest.fixture(name="recorded")
def recorded_fixture() -> Recorded:
    """Collect the profiles the gateway records."""
    return []


@pytest.fixture(name="statuses")
def statuses_fixture() -> list[UpstreamStatus]:
    """Collect what the gateway reports about reaching OJ's gateway."""
    return []


@pytest.fixture(name="gateway")
async def gateway_fixture(
    certificate: tuple[Path, Path],
    cloud: FakeCloud,
    events: Events,
    recorded: Recorded,
    statuses: list[UpstreamStatus],
) -> AsyncIterator[WG4Gateway]:
    """Run a relaying gateway that knows one thermostat, without a profile."""
    gateway = WG4Gateway(
        server_ssl_context(*certificate),
        mode=GatewayMode.RELAY,
        endpoint=GatewayEndpoint(
            host=WG4_NORTH_AMERICA.host,
            port=cloud.port,
            upstream_ip="127.0.0.1",
            pin_sha256=cloud.fingerprint,
        ),
        listen_host="127.0.0.1",
        listen_port=0,
    )
    gateway.allowed_serials = {SERIAL}
    gateway.on_state = lambda serial, fields: events.append((serial, fields))
    gateway.on_profile = lambda serial, profile: recorded.append((serial, profile))
    gateway.on_upstream = statuses.append
    await gateway.start()
    yield gateway
    await gateway.stop()


async def _relayed(connect: Callable[[], Any]) -> FakeThermostat:
    """Connect a thermostat that has logged in and uploaded its state."""
    thermostat: FakeThermostat = await connect()
    await thermostat.login()
    await thermostat.upload()
    return thermostat


async def test_relayed_session(  # noqa: PLR0913, PLR0917 # pylint: disable=too-many-arguments
    gateway: WG4Gateway,
    cloud: FakeCloud,
    events: Events,
    recorded: Recorded,
    statuses: list[UpstreamStatus],
    connect: Callable[[], Any],
) -> None:
    """A relayed thermostat talks to OJ's gateway, and its profile is recorded."""
    thermostat: FakeThermostat = await connect()

    # OJ's gateway gets the login as the thermostat sent it, and answers it.
    assert (await thermostat.login()).varint(3) == 1
    assert cloud.received == [Message.parse(login())]
    assert gateway.connected == {SERIAL}
    assert gateway.is_relayed(SERIAL)
    assert gateway.upstream_status is UpstreamStatus.CONNECTED
    assert statuses == [UpstreamStatus.CONNECTED]
    assert events == [(SERIAL, {"Online": True})]

    # The uploads are answered by OJ's gateway; there is no profile yet.
    assert await thermostat.upload() == FROM_THE_CLOUD
    assert cloud.received[1:] == [
        Message.parse(SCHEDULE_UPLOAD),
        Message.parse(STATE_UPLOAD),
    ]
    assert events[-1] == (SERIAL, UPLOADED)

    # Those answers are the thermostat's profile from now on.
    await until(lambda: bool(recorded))
    assert recorded == [(SERIAL, CLOUD_PROFILE)]
    assert gateway.profiles == {SERIAL: CLOUD_PROFILE}
    # The history shows what OJ's gateway sent, which answers how it behaves.
    history = "\n".join(gateway.history)
    assert f"Thermostat {SERIAL} logged in; it is relayed to OJ's gateway" in history
    assert f"OJ's gateway (thermostat {SERIAL}): seq=1 fields=1,5,10" in history
    assert f"OJ's gateway (thermostat {SERIAL}): seq=0 fields=1,5,18" in history
    assert f"Recorded the profile of thermostat {SERIAL}" in history

    # What the thermostat reports goes to OJ's gateway and to the listener.
    await thermostat.send(TELEMETRY)
    assert (await thermostat.receive()).varint(1) == 403
    assert cloud.received[-1] == Message.parse(TELEMETRY)
    assert events[-1] == (SERIAL, {"Temperature": 2033, "Heating": False})


async def test_pin_may_be_written_with_colons(
    gateway: WG4Gateway, cloud: FakeCloud, connect: Callable[[], Any]
) -> None:
    """A fingerprint as tools print it, in capitals with colons, is accepted."""
    pairs = [cloud.fingerprint[i : i + 2] for i in range(0, len(cloud.fingerprint), 2)]
    gateway.endpoint = replace(gateway.endpoint, pin_sha256=":".join(pairs).upper())

    thermostat: FakeThermostat = await connect()
    await thermostat.login()
    assert gateway.is_relayed(SERIAL)


async def test_profile_is_recorded_when_it_changes(
    gateway: WG4Gateway,
    cloud: FakeCloud,
    recorded: Recorded,
    connect: Callable[[], Any],
) -> None:
    """The profile follows the answers of OJ's gateway, without repeating them."""
    await _relayed(connect)
    await until(lambda: len(recorded) == 1)

    # The same answers on the next connection are not recorded again.
    await _relayed(connect)

    # Other answers, for instance after a change in the app, replace the profile.
    cloud.profile = PROFILE
    await _relayed(connect)
    await until(lambda: len(recorded) == 2)
    assert recorded == [(SERIAL, CLOUD_PROFILE), (SERIAL, PROFILE)]
    assert gateway.profiles == {SERIAL: PROFILE}


async def test_command_goes_straight_to_a_relayed_thermostat(
    gateway: WG4Gateway, cloud: FakeCloud, events: Events, connect: Callable[[], Any]
) -> None:
    """OJ's gateway does not see a command of the gateway, nor its answer."""
    thermostat = await _relayed(connect)
    passed_on = len(cloud.received)

    task = asyncio.create_task(gateway.set_mode(SERIAL, 3, 2250))
    assert (await thermostat.receive()).varint(1) == 101
    await thermostat.send(acknowledgement(101))
    await task
    assert events[-1][1]["ManualTemperature"] == 2250

    # An answer to a message of OJ's gateway is passed on. That also goes for
    # one with the number of the command, now that the command has its answer.
    await thermostat.send(acknowledgement(0))
    await thermostat.send(acknowledgement(101))
    await until(lambda: len(cloud.received) == passed_on + 2)
    assert cloud.received[passed_on:] == [
        Message.parse(acknowledgement(0)),
        Message.parse(acknowledgement(101)),
    ]


async def test_answers_are_matched_in_the_order_of_sending(
    gateway: WG4Gateway, cloud: FakeCloud, connect: Callable[[], Any]
) -> None:
    """A number that both gateways used does not mix up their answers."""
    thermostat = await _relayed(connect)
    passed_on = len(cloud.received)

    # The gateway sends command 101, and then OJ's gateway uses that number.
    task = asyncio.create_task(gateway.set_mode(SERIAL, 3, 2250))
    assert (await thermostat.receive()).varint(1) == 101
    from_the_app = command(101, changed_state(STATE, 1), SCHEDULE)
    await cloud.send(from_the_app)
    assert await thermostat.receive_frame() == encode_frame(from_the_app)

    # The thermostat answers in that order: the first answer is the gateway's.
    await thermostat.send(acknowledgement(101))
    await task
    assert len(cloud.received) == passed_on
    await thermostat.send(acknowledgement(101))
    await until(lambda: len(cloud.received) == passed_on + 1)
    assert cloud.received[-1] == Message.parse(acknowledgement(101))


async def test_command_avoids_a_number_that_is_waiting_for_an_answer(
    gateway: WG4Gateway, cloud: FakeCloud, connect: Callable[[], Any]
) -> None:
    """The gateway skips a number OJ's gateway has an answer outstanding for."""
    thermostat = await _relayed(connect)
    passed_on = len(cloud.received)

    from_the_app = command(101, changed_state(STATE, 1), SCHEDULE)
    await cloud.send(from_the_app)
    assert await thermostat.receive_frame() == encode_frame(from_the_app)

    task = asyncio.create_task(gateway.set_mode(SERIAL, 3, 2250))
    assert (await thermostat.receive()).varint(1) == 102
    await thermostat.send(acknowledgement(102))
    await task
    assert len(cloud.received) == passed_on


async def test_change_in_the_app_reaches_the_thermostat(
    gateway: WG4Gateway, cloud: FakeCloud, events: Events, connect: Callable[[], Any]
) -> None:
    """A command of OJ's gateway is passed on, and reported once it is accepted."""
    thermostat = await _relayed(connect)
    answered = len(cloud.received)

    # The app puts the thermostat on its schedule.
    from_the_app = command(7, changed_state(STATE, 1), SCHEDULE)
    await cloud.send(from_the_app)
    assert await thermostat.receive_frame() == encode_frame(from_the_app)
    # Nothing has changed until the thermostat takes the command over.
    assert events[-1][1].get("RegulationMode") != 1
    await thermostat.send(acknowledgement(7))
    await until(lambda: events[-1][1].get("RegulationMode") == 1)
    await until(lambda: len(cloud.received) == answered + 1)

    # The next command of the gateway starts from that state.
    task = asyncio.create_task(gateway.set_mode(SERIAL, 3))
    sent = await thermostat.receive()
    state = sent.message(9)
    assert state is not None
    assert state.varint(2) == 3
    assert state.message(5) == Message.parse(encode_field(1, 4444))
    await thermostat.send(acknowledgement(101))
    await task


async def test_commands_count_in_the_order_they_are_acknowledged(
    gateway: WG4Gateway, cloud: FakeCloud, events: Events, connect: Callable[[], Any]
) -> None:
    """A change in the app that follows a command of the gateway has the last word.

    The thermostat answers in the order it received, which is the order in
    which it took the states over.
    """
    thermostat = await _relayed(connect)

    task = asyncio.create_task(gateway.set_mode(SERIAL, 3, 2250))
    assert (await thermostat.receive()).varint(1) == 101
    # Before the thermostat answers, the app puts it on its schedule.
    from_the_app = command(7, changed_state(STATE, 1), SCHEDULE)
    await cloud.send(from_the_app)
    assert await thermostat.receive_frame() == encode_frame(from_the_app)

    await thermostat.send(acknowledgement(101))
    await task
    assert events[-1][1]["RegulationMode"] == 3
    await thermostat.send(acknowledgement(7))
    await until(lambda: events[-1][1].get("RegulationMode") == 1)

    # The next command starts from the state of the app, not from the
    # gateway's own command.
    task = asyncio.create_task(gateway.set_mode(SERIAL, 3))
    sent = await thermostat.receive()
    state = sent.message(9)
    assert state is not None
    assert state.message(5) == Message.parse(encode_field(1, 4444))
    await thermostat.send(acknowledgement(102))
    await task


async def test_command_starts_from_what_ojs_gateway_has_under_way(
    gateway: WG4Gateway, cloud: FakeCloud, events: Events, connect: Callable[[], Any]
) -> None:
    """A command does not undo a change of the app that is still on its way.

    The thermostat takes the state of the app over first and the command of
    the gateway, a complete state, right after it.
    """
    thermostat = await _relayed(connect)

    # The app sets another permanent hold, which the thermostat has not
    # answered yet when the gateway puts it on its schedule.
    await cloud.send(command(7, changed_state(STATE, 3, 2500), SCHEDULE))
    await thermostat.receive_frame()
    task = asyncio.create_task(gateway.set_mode(SERIAL, 1))
    sent = await thermostat.receive()
    state = sent.message(9)
    assert state is not None
    assert state.varint(2) == 1
    assert state.message(5) == Message.parse(encode_field(1, 5000))

    await thermostat.send(acknowledgement(7))
    await thermostat.send(acknowledgement(101))
    await task
    assert events[-1][1]["RegulationMode"] == 1
    assert events[-1][1]["ManualTemperature"] == 2500


async def test_change_of_ojs_gateway_without_an_answer_does_not_stay_under_way(
    gateway: WG4Gateway,
    cloud: FakeCloud,
    connect: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A change the thermostat never confirmed counts as lost after a while."""
    thermostat = await _relayed(connect)
    await cloud.send(command(7, changed_state(STATE, 3, 2500), SCHEDULE))
    await thermostat.receive_frame()

    monkeypatch.setattr(gateway_server, "LATE_ACKNOWLEDGE_TIMEOUT", 0)
    task = asyncio.create_task(gateway.set_mode(SERIAL, 1))
    state = (await thermostat.receive()).message(9)
    assert state is not None
    assert state.message(5) == Message.parse(encode_field(1, 4444))
    await thermostat.send(acknowledgement(101))
    await task


async def test_refused_command_of_ojs_gateway_changes_nothing(
    cloud: FakeCloud, events: Events, connect: Callable[[], Any]
) -> None:
    """A state the thermostat did not take over is not reported."""
    thermostat = await _relayed(connect)
    seen = len(events)

    from_the_app = command(7, changed_state(STATE, 1), SCHEDULE)
    await cloud.send(from_the_app)
    await thermostat.receive_frame()
    answered = len(cloud.received)
    await thermostat.send(acknowledgement(7, accepted=0))
    await until(lambda: len(cloud.received) == answered + 1)
    assert len(events) == seen


async def test_relay_passes_on_what_it_does_not_understand(
    gateway: WG4Gateway, cloud: FakeCloud, events: Events, connect: Callable[[], Any]
) -> None:
    """A message of OJ's gateway reaches the thermostat whatever is in it."""
    thermostat = await _relayed(connect)
    reported = len(events)

    # Not a message at all, and a command whose state is not a message.
    for message in (b"\x00", encode_field(1, 5) + encode_field(9, b"\x00")):
        await cloud.send(message)
        assert await thermostat.receive_frame() == encode_frame(message)
    assert "\n".join(gateway.history).count("that is not understood") == 2
    # Accepting such a command teaches the gateway nothing either.
    answered = len(cloud.received)
    await thermostat.send(acknowledgement(5))
    await until(lambda: len(cloud.received) == answered + 1)
    assert len(events) == reported

    # The relay goes on, and a command still starts from the last good state.
    task = asyncio.create_task(gateway.set_mode(SERIAL, 1))
    sent = await thermostat.receive()
    state = sent.message(9)
    assert state is not None
    assert state.message(5) == Message.parse(encode_field(1, 4444))
    await thermostat.send(acknowledgement(101))
    await task


async def test_relay_ends_when_ojs_gateway_hangs_up(
    gateway: WG4Gateway, cloud: FakeCloud, events: Events, connect: Callable[[], Any]
) -> None:
    """Without OJ's gateway the thermostat is disconnected, to connect again."""
    thermostat: FakeThermostat = await connect()
    await thermostat.login()
    await cloud.hang_up()

    assert await thermostat.was_closed()
    await until(lambda: not gateway.connected)
    assert events[-1] == (SERIAL, {"Online": False})


async def test_relay_ends_when_the_thermostat_hangs_up(
    gateway: WG4Gateway, cloud: FakeCloud, connect: Callable[[], Any]
) -> None:
    """The connection to OJ's gateway does not outlive the thermostat's."""
    thermostat: FakeThermostat = await connect()
    await thermostat.login()
    await thermostat.close()

    await until(lambda: cloud.hung_up == 1)
    assert not gateway.connected


async def test_reconnect_replaces_the_relayed_connection(
    gateway: WG4Gateway, cloud: FakeCloud, connect: Callable[[], Any]
) -> None:
    """A thermostat that connects again gets a new connection to OJ's gateway."""
    first: FakeThermostat = await connect()
    await first.login()
    second: FakeThermostat = await connect()
    await second.login()

    assert await first.was_closed()
    await until(lambda: cloud.hung_up == 1)
    assert cloud.connections == 2
    assert gateway.is_relayed(SERIAL)


async def test_relay_passes_on_what_the_thermostat_says_unknowingly(
    gateway: WG4Gateway, cloud: FakeCloud, events: Events, connect: Callable[[], Any]
) -> None:
    """A message the gateway cannot read still reaches OJ's gateway.

    The relay goes on: a firmware this library does not know must not cut the
    thermostat off from the vendor's app.
    """
    thermostat = await _relayed(connect)
    reported = len(events)
    passed_on = len(cloud.received)

    # Not a message at all, and a state that is not a message. Neither wants
    # an answer of OJ's gateway.
    await thermostat.send(b"\x00")
    unreadable = encode_field(9, b"\x00")
    await thermostat.send(unreadable)
    await until(lambda: len(cloud.received) == passed_on + 1)
    assert cloud.not_understood == [b"\x00"]
    assert cloud.received[-1] == Message.parse(unreadable)
    assert len(events) == reported
    history = "\n".join(gateway.history)
    assert "a message of 1 bytes that is not understood" in history
    assert "sent a message that is not understood" in history

    # The relay goes on, and a command still starts from the last good state.
    task = asyncio.create_task(gateway.set_mode(SERIAL, 1))
    sent = await thermostat.receive()
    state = sent.message(9)
    assert state is not None
    assert state.message(5) == Message.parse(encode_field(1, 4444))
    await thermostat.send(acknowledgement(101))
    await task
    assert gateway.is_relayed(SERIAL)


async def test_relay_survives_fields_it_reads_differently(
    gateway: WG4Gateway, cloud: FakeCloud, connect: Callable[[], Any]
) -> None:
    """A message that another firmware fills differently is passed on.

    Neither a field 6 that is no login, nor an end no calendar holds, may end
    the relay: describing a message for the log must not close a connection.
    """
    thermostat = await _relayed(connect)
    passed_on = len(cloud.received)

    no_login = encode_field(1, 50) + encode_field(5, 1) + encode_field(6, b"\x00")
    endless = encode_field(4, encode_field(1, 3332) + encode_field(2, 2**64 - 1))
    hold = encode_field(1, 51) + encode_field(5, 1) + encode_field(9, endless)
    for message in (no_login, hold):
        await thermostat.send(message)
    await until(lambda: len(cloud.received) == passed_on + 2)
    assert cloud.received[-2:] == [Message.parse(no_login), Message.parse(hold)]
    assert f"until={2**64 - 1}" in "\n".join(gateway.history)
    assert gateway.is_relayed(SERIAL)


async def test_log_line_never_ends_a_connection(
    gateway: WG4Gateway,
    cloud: FakeCloud,
    connect: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A message that cannot be described is named by its fields."""
    thermostat = await _relayed(connect)
    passed_on = len(cloud.received)

    def broken(_message: Message) -> str:
        raise OverflowError

    monkeypatch.setattr(gateway, "_describe", broken)
    await thermostat.send(TELEMETRY)
    await until(lambda: len(cloud.received) == passed_on + 1)
    assert "(cannot be described)" in "\n".join(gateway.history)
    assert gateway.is_relayed(SERIAL)


async def test_lost_command_does_not_take_the_answer_of_ojs_gateway(
    gateway: WG4Gateway,
    cloud: FakeCloud,
    connect: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A command that was never answered gives up its number in time.

    OJ's gateway counts from 0 and can reach the number of a command that the
    thermostat never answered. The thermostat's answer is then OJ's.
    """
    monkeypatch.setattr(gateway_server, "ACKNOWLEDGE_TIMEOUT", 0.05)
    thermostat = await _relayed(connect)
    passed_on = len(cloud.received)

    with pytest.raises(OJMicrolineTimeoutError):
        await gateway.set_mode(SERIAL, 3, 2250)
    assert (await thermostat.receive()).varint(1) == 101

    from_the_app = command(101, changed_state(STATE, 1), SCHEDULE)
    await cloud.send(from_the_app)
    assert await thermostat.receive_frame() == encode_frame(from_the_app)
    await thermostat.send(acknowledgement(101))
    await until(lambda: len(cloud.received) == passed_on + 1)
    assert cloud.received[-1] == Message.parse(acknowledgement(101))
    assert "never answered command 101" in "\n".join(gateway.history)


async def test_late_acknowledgement_counts_for_a_while(
    gateway: WG4Gateway,
    cloud: FakeCloud,
    events: Events,
    connect: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After LATE_ACKNOWLEDGE_TIMEOUT a command counts as lost."""
    monkeypatch.setattr(gateway_server, "ACKNOWLEDGE_TIMEOUT", 0.01)
    monkeypatch.setattr(gateway_server, "LATE_ACKNOWLEDGE_TIMEOUT", 0.05)
    thermostat = await _relayed(connect)
    passed_on = len(cloud.received)
    with pytest.raises(OJMicrolineTimeoutError):
        await gateway.set_mode(SERIAL, 3, 2250)
    assert (await thermostat.receive()).varint(1) == 101
    await asyncio.sleep(0.06)

    # The next command forgets the lost one; its answer is no longer taken.
    task = asyncio.create_task(gateway.set_mode(SERIAL, 3, 2300))
    assert (await thermostat.receive()).varint(1) == 102
    await thermostat.send(acknowledgement(101))
    await thermostat.send(acknowledgement(102))
    await task
    assert events[-1][1]["ManualTemperature"] == 2300
    await until(lambda: len(cloud.received) == passed_on + 1)
    assert cloud.received[-1] == Message.parse(acknowledgement(101))


@pytest.mark.parametrize(
    "data",
    [
        encode_frame(login()),
        b"\x09",
    ],
)
async def test_relay_ends_after_a_malformed_message(
    data: bytes, gateway: WG4Gateway, cloud: FakeCloud, connect: Callable[[], Any]
) -> None:
    """Nonsense from the thermostat is not passed on to OJ's gateway."""
    thermostat: FakeThermostat = await connect()
    await thermostat.login()
    await thermostat.send_raw(data)

    assert await thermostat.was_closed()
    await until(lambda: cloud.hung_up == 1)
    assert cloud.received == [Message.parse(login())]
    assert not gateway.connected


async def test_quiet_relayed_thermostat_is_disconnected(
    gateway: WG4Gateway,
    cloud: FakeCloud,
    connect: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A relayed thermostat that stops sending heartbeats is dropped too."""
    monkeypatch.setattr(gateway_server, "IDLE_TIMEOUT", 0.05)
    thermostat: FakeThermostat = await connect()
    await thermostat.login()

    assert await thermostat.was_closed()
    await until(lambda: cloud.hung_up == 1)
    assert not gateway.connected


async def test_stop_ends_the_relay(
    gateway: WG4Gateway, cloud: FakeCloud, connect: Callable[[], Any]
) -> None:
    """Stopping the gateway closes both sides of a relayed connection."""
    thermostat: FakeThermostat = await connect()
    await thermostat.login()

    await gateway.stop()

    assert await thermostat.was_closed()
    await until(lambda: cloud.hung_up == 1)
    assert not gateway.connected


async def test_untrusted_gateway_gets_nothing(  # noqa: PLR0913, PLR0917 # pylint: disable=too-many-arguments
    gateway: WG4Gateway,
    cloud: FakeCloud,
    recorded: Recorded,
    statuses: list[UpstreamStatus],
    connect: Callable[[], Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A certificate that does not match the pin ends the relay before it starts."""
    gateway.endpoint = replace(gateway.endpoint, pin_sha256="00" * 32)

    # Without a profile the thermostat cannot be answered locally either.
    thermostat: FakeThermostat = await connect()
    await thermostat.send(login())
    assert await thermostat.was_closed()
    assert statuses == [UpstreamStatus.UNTRUSTED]
    assert "does not match the pin" in caplog.text
    # The fingerprint that was seen is what a new pin would have to be.
    assert gateway.upstream_fingerprint == cloud.fingerprint
    assert f"Its SHA-256 fingerprint is {cloud.fingerprint}" in caplog.text
    assert f"fingerprint is {cloud.fingerprint}" in "\n".join(gateway.history)

    # With a profile it is answered locally, without asking OJ's gateway
    # again: it would only show the same certificate.
    gateway.profiles[SERIAL] = PROFILE
    again: FakeThermostat = await connect()
    assert (await again.login()).varint(3) == 1
    assert await again.upload() == FROM_THE_PROFILE
    assert gateway.connected == {SERIAL}
    assert not gateway.is_relayed(SERIAL)
    assert "it could not be used 0 seconds ago" in "\n".join(gateway.history)

    # OJ's gateway saw one connection come and go without a single message,
    # and the problem was reported once.
    await until(lambda: cloud.hung_up == 1)
    assert cloud.connections == 1
    assert not cloud.received
    assert not recorded
    assert statuses == [UpstreamStatus.UNTRUSTED]


async def test_thermostat_is_answered_locally_while_ojs_gateway_is_down(
    gateway: WG4Gateway,
    cloud: FakeCloud,
    statuses: list[UpstreamStatus],
    connect: Callable[[], Any],
) -> None:
    """A stored profile keeps a thermostat connected when OJ's gateway is gone."""
    await cloud.stop()

    # Without a profile the thermostat has to try again later.
    thermostat: FakeThermostat = await connect()
    await thermostat.send(login())
    assert await thermostat.was_closed()
    assert statuses == [UpstreamStatus.UNREACHABLE]
    events = "\n".join(gateway.history)
    assert "OJ's gateway at 127.0.0.1 cannot be used: " in events
    assert f"Refusing thermostat {SERIAL}: it has no profile" in events

    gateway.profiles[SERIAL] = PROFILE
    again: FakeThermostat = await connect()
    assert (await again.login()).varint(3) == 1
    assert await again.upload() == FROM_THE_PROFILE
    assert not gateway.is_relayed(SERIAL)
    assert gateway.upstream_status is UpstreamStatus.UNREACHABLE


async def test_ojs_gateway_is_left_alone_after_it_was_unreachable(
    gateway: WG4Gateway,
    cloud: FakeCloud,
    connect: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A thermostat that connects again is answered at once.

    It gives up on a login within seconds. Waiting for OJ's gateway on every
    new connection would keep it from ever being answered.
    """

    def attempts() -> int:
        return "\n".join(gateway.history).count("cannot be used")

    await cloud.stop()

    # Without a profile OJ's gateway is the only way, so it is tried each time.
    for expected in (1, 2):
        refused: FakeThermostat = await connect()
        await refused.send(login())
        assert await refused.was_closed()
        assert attempts() == expected

    gateway.profiles[SERIAL] = PROFILE
    thermostat: FakeThermostat = await connect()
    assert (await thermostat.login()).varint(3) == 1
    assert attempts() == 2
    assert f"Not waiting for OJ's gateway for thermostat {SERIAL}" in "\n".join(
        gateway.history
    )
    assert not gateway.is_relayed(SERIAL)

    # After the interval a new connection tries OJ's gateway again.
    monkeypatch.setattr(gateway_server, "UPSTREAM_RETRY_INTERVAL", 0)
    again: FakeThermostat = await connect()
    assert (await again.login()).varint(3) == 1
    assert attempts() == 3


async def test_gateway_that_does_not_answer_counts_as_unreachable(
    gateway: WG4Gateway,
    statuses: list[UpstreamStatus],
    connect: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connection to OJ's gateway that hangs is given up on."""
    monkeypatch.setattr(gateway_server, "UPSTREAM_TIMEOUT", 0.05)
    release = asyncio.Event()

    async def silent(
        _reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await release.wait()
        writer.close()

    server = await asyncio.start_server(silent, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    gateway.endpoint = replace(gateway.endpoint, port=port)
    try:
        thermostat: FakeThermostat = await connect()
        await thermostat.send(login())
        assert await thermostat.was_closed()
        assert statuses == [UpstreamStatus.UNREACHABLE]
    finally:
        release.set()
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize("behaviour", ["silent", "hangs_up_at_login"])
async def test_ojs_gateway_that_takes_no_login_counts_as_unreachable(  # noqa: PLR0913, PLR0917 # pylint: disable=too-many-arguments
    gateway: WG4Gateway,
    cloud: FakeCloud,
    statuses: list[UpstreamStatus],
    connect: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
    behaviour: str,
) -> None:
    """A gateway that connects but does not answer the login is no use.

    The thermostat is answered locally, at once and on its next connection,
    instead of being relayed to it again and again.
    """
    monkeypatch.setattr(gateway_server, "UPSTREAM_TIMEOUT", 0.2)
    setattr(cloud, behaviour, True)
    gateway.profiles[SERIAL] = PROFILE

    thermostat: FakeThermostat = await connect()
    assert (await thermostat.login()).varint(3) == 1
    assert await thermostat.upload() == FROM_THE_PROFILE
    assert not gateway.is_relayed(SERIAL)
    assert statuses == [UpstreamStatus.UNREACHABLE]
    assert "did not answer the login of thermostat" in "\n".join(gateway.history)

    # The next connection does not wait for it again.
    again: FakeThermostat = await connect()
    assert (await again.login()).varint(3) == 1
    assert cloud.connections == 1


async def test_ignored_login_says_nothing_about_other_thermostats(
    gateway: WG4Gateway,
    cloud: FakeCloud,
    connect: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OJ's gateway is still tried for a thermostat it did not let down."""
    monkeypatch.setattr(gateway_server, "UPSTREAM_TIMEOUT", 0.2)
    other = "7654321"
    gateway.allowed_serials = {SERIAL, other}
    gateway.profiles[SERIAL] = gateway.profiles[other] = PROFILE

    cloud.hangs_up_at_login = True
    ignored: FakeThermostat = await connect()
    assert (await ignored.login()).varint(3) == 1
    assert not gateway.is_relayed(SERIAL)

    cloud.hangs_up_at_login = False
    thermostat: FakeThermostat = await connect()
    await thermostat.login(other)
    assert gateway.is_relayed(other)
    assert gateway.upstream_status is UpstreamStatus.CONNECTED


async def test_silent_ojs_gateway_does_not_make_a_thermostat_connect_again(
    gateway: WG4Gateway,
    cloud: FakeCloud,
    connect: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a gateway that answers the login is worth connecting again for.

    A thermostat cannot be reached while it connects again, so one that
    would only be answered locally once more is left alone.
    """
    monkeypatch.setattr(gateway_server, "UPSTREAM_TIMEOUT", 0.05)
    monkeypatch.setattr(gateway_server, "UPSTREAM_RETRY_INTERVAL", 0.05)
    gateway.profiles[SERIAL] = PROFILE
    cloud.silent = True

    thermostat: FakeThermostat = await connect()
    assert (await thermostat.login()).varint(3) == 1
    # OJ's gateway takes the connection every time, and never answers.
    await until(lambda: cloud.connections >= 3)
    assert gateway.connected == {SERIAL}
    assert not gateway.is_relayed(SERIAL)

    cloud.silent = False
    assert await thermostat.was_closed()
    again: FakeThermostat = await connect()
    await again.login()
    assert gateway.is_relayed(SERIAL)


async def test_stopping_during_a_login_hangs_up_on_ojs_gateway(
    gateway: WG4Gateway, cloud: FakeCloud, connect: Callable[[], Any]
) -> None:
    """A gateway that stops before OJ's gateway answered leaves nothing open."""
    cloud.silent = True
    thermostat: FakeThermostat = await connect()
    await thermostat.send(login())
    await until(lambda: len(cloud.received) == 1)

    await gateway.stop()
    await until(lambda: cloud.hung_up == 1)


async def test_ojs_gateway_that_takes_no_login_is_the_only_way_without_a_profile(
    gateway: WG4Gateway,
    cloud: FakeCloud,
    connect: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a profile the thermostat cannot be answered here either."""
    monkeypatch.setattr(gateway_server, "UPSTREAM_TIMEOUT", 0.05)
    cloud.silent = True

    for attempts in (1, 2):
        thermostat: FakeThermostat = await connect()
        await thermostat.send(login())
        assert await thermostat.was_closed()
        assert cloud.connections == attempts
    assert not gateway.connected


async def test_network_error_is_a_closed_connection(
    gateway: WG4Gateway,
    connect: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A network that goes away closes the connection without a traceback."""

    async def unreachable(_reader: asyncio.StreamReader) -> tuple[str, bytes]:
        raise OSError(101, "Network is unreachable")

    monkeypatch.setattr(gateway, "_read_login", unreachable)
    thermostat: FakeThermostat = await connect()
    assert await thermostat.was_closed()
    assert "was closed: OSError(101, 'Network is unreachable')" in gateway.history[-1]


async def test_thermostat_is_relayed_again_when_ojs_gateway_is_back(
    gateway: WG4Gateway,
    cloud: FakeCloud,
    statuses: list[UpstreamStatus],
    connect: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gateway keeps trying OJ's gateway, and reconnects the thermostat."""
    monkeypatch.setattr(gateway_server, "UPSTREAM_RETRY_INTERVAL", 0.05)
    gateway.profiles[SERIAL] = PROFILE
    port = cloud.port
    await cloud.stop()

    thermostat: FakeThermostat = await connect()
    await thermostat.login()
    assert not gateway.is_relayed(SERIAL)
    # OJ's gateway stays down for a few attempts.
    await asyncio.sleep(0.2)
    assert gateway.connected == {SERIAL}

    await cloud.start(port)
    # The gateway hangs up, which makes a thermostat connect again.
    assert await thermostat.was_closed()
    assert statuses == [UpstreamStatus.UNREACHABLE, UpstreamStatus.CONNECTED]

    again: FakeThermostat = await connect()
    await again.login()
    assert gateway.is_relayed(SERIAL)


async def test_retries_stop_when_the_thermostat_disconnects(
    gateway: WG4Gateway,
    cloud: FakeCloud,
    connect: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OJ's gateway is only tried for a thermostat that is still connected."""
    monkeypatch.setattr(gateway_server, "UPSTREAM_RETRY_INTERVAL", 0.05)
    gateway.profiles[SERIAL] = PROFILE
    port = cloud.port
    await cloud.stop()

    thermostat: FakeThermostat = await connect()
    await thermostat.login()
    await thermostat.close()
    await until(lambda: not gateway.connected)

    await cloud.start(port)
    await asyncio.sleep(0.2)
    assert cloud.connections == 0


async def test_local_mode_never_contacts_ojs_gateway(
    gateway: WG4Gateway, cloud: FakeCloud, connect: Callable[[], Any]
) -> None:
    """In local mode a thermostat is answered locally, or not at all."""
    gateway.mode = GatewayMode.LOCAL

    without_profile: FakeThermostat = await connect()
    await without_profile.send(login())
    assert await without_profile.was_closed()

    gateway.profiles[SERIAL] = PROFILE
    thermostat: FakeThermostat = await connect()
    await thermostat.login()
    assert await thermostat.upload() == FROM_THE_PROFILE

    assert not gateway.is_relayed(SERIAL)
    assert gateway.upstream_status is None
    assert cloud.connections == 0


async def test_listeners_of_the_relay_may_fail(
    gateway: WG4Gateway,
    connect: Callable[[], Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A broken listener does not break the relay."""

    def broken(*_arguments: Any) -> None:
        msg = "boom"
        raise RuntimeError(msg)

    gateway.on_profile = broken
    gateway.on_upstream = broken
    thermostat = await _relayed(connect)

    await thermostat.send(TELEMETRY)
    assert (await thermostat.receive()).varint(1) == 403
    assert gateway.profiles == {SERIAL: CLOUD_PROFILE}
    assert caplog.text.count("Error in a listener of the gateway") == 2
