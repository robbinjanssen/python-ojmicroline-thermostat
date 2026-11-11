"""Messages between a WG4-series thermostat and its device gateway.

There is no published specification. The field numbers and their meaning are
as observed by peskyAdmin/schluter-local (MIT, see the LICENSE file in this
package), from which this module is adapted.

Every message has a sequence number in field 1. An acknowledgement repeats
that number with field 3 set to 1. Field 5 is 1 on most messages in both
directions; what it means is not known.

From the thermostat:

    field 6          login: 6.1 the account's email address, 6.2 the serial
                     number
    field 2 = 6      upload of the schedule and settings
    field 2 = 4      full upload after login: 7 device info, 8 schedule,
                     9 state
    field 9 alone    someone changed the thermostat at the wall
    field 7 alone    the device info changed
    field 15         heartbeat, about once a minute: 15.1 the floor
                     temperature, 15.2 whether the floor is heating
    fields 1 and 3   acknowledgement of a command

To the thermostat:

    field 12         in the answer to a login: 12.1 the server time
    field 10         configuration, the answer to the full upload
    field 18         support information, the answer to the schedule upload
    fields 8 and 9   a command: the schedule and the state the thermostat
                     has to take over

State (field 9): 9.1 the time of the change, 9.2 the regulation mode, 9.4 the
temporary hold (9.4.1 its setpoint, 9.4.2 its end time) and 9.5 the permanent
hold (9.5.1 its setpoint). The thermostat keeps both setpoints all the time
and uses the one of the current mode. Device info 7.2 is the room name, and
7.9 the setpoint the thermostat is aiming for right now, in any mode.

Setpoints on the wire are in 1/200 °C, the floor temperature in 1/100 °C. This
library uses 1/100 °C everywhere. Times are seconds since 2000-01-01 UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ojmicroline_thermostat.const import (
    REGULATION_COMFORT,
    REGULATION_MANUAL,
    REGULATION_SCHEDULE,
    REGULATION_VACATION,
)

from .protobuf import Message, encode_field, set_field

FIELD_SEQUENCE = 1
FIELD_KIND = 2
FIELD_ACKNOWLEDGED = 3
FIELD_LOGIN = 6
FIELD_DEVICE_INFO = 7
FIELD_SCHEDULE = 8
FIELD_STATE = 9
FIELD_CONFIGURATION = 10
FIELD_SERVER_TIME = 12
FIELD_TELEMETRY = 15
FIELD_SUPPORT = 18

# Values of FIELD_KIND in the uploads that follow a login.
KIND_STATE_UPLOAD = 4
KIND_SCHEDULE_UPLOAD = 6

EPOCH = datetime(2000, 1, 1, tzinfo=UTC)

# Setpoints on the wire are in 1/200 °C.
_WIRE_SCALE = 200

_STATE_TIME = 1
_STATE_MODE = 2
_TEMPORARY_HOLD = 4
_PERMANENT_HOLD = 5
_HOLD_SETPOINT = 1
_HOLD_END_TIME = 2
_LOGIN_SERIAL = 2
_INFO_ROOM = 2
_INFO_TARGET = 9
_TELEMETRY_FLOOR = 1
_TELEMETRY_HEATING = 2
# Field 5 is 1 on most messages in both directions; its meaning is not known.
_FIELD_5 = 5

# The field of the state that holds the setpoint of a mode, and the name of
# that setpoint in the cloud API.
_HOLD_FIELDS = {
    REGULATION_COMFORT: _TEMPORARY_HOLD,
    REGULATION_MANUAL: _PERMANENT_HOLD,
}
_HOLD_NAMES = {
    _TEMPORARY_HOLD: "ComfortTemperature",
    _PERMANENT_HOLD: "ManualTemperature",
}
_MODE_NAMES = {
    REGULATION_SCHEDULE: "schedule",
    REGULATION_COMFORT: "temporary hold",
    REGULATION_MANUAL: "permanent hold",
    REGULATION_VACATION: "vacation",
}


def device_time(moment: datetime | None = None) -> int:
    """Return a moment the way the thermostat counts time.

    Args:
    ----
        moment: A timezone-aware moment; now when omitted.

    Returns:
    -------
        The seconds since 2000-01-01 UTC.

    """
    if moment is None:
        moment = datetime.now(tz=UTC)
    return int((moment - EPOCH).total_seconds())


def acknowledgement(sequence: int) -> bytes:
    """Build the message that acknowledges a message of the thermostat."""
    return encode_field(FIELD_SEQUENCE, sequence) + encode_field(FIELD_ACKNOWLEDGED, 1)


def login_acknowledgement(sequence: int, moment: datetime | None = None) -> bytes:
    """Build the answer to a login, which also tells the thermostat the time.

    Args:
    ----
        sequence: The sequence number of the login.
        moment: The server time as a timezone-aware moment; now when omitted.

    Returns:
    -------
        The encoded message.

    """
    return (
        acknowledgement(sequence)
        # OJ's gateway sets field 4 to 1 in this answer.
        + encode_field(4, 1)
        + encode_field(FIELD_SERVER_TIME, encode_field(1, device_time(moment)))
    )


def login_serial(message: Message) -> str | None:
    """Return the serial number from a login, or None if there is none.

    Raises
    ------
        ValueError: The login is malformed.

    """
    login = message.message(FIELD_LOGIN)
    if login is None:
        return None
    serial = login.data(_LOGIN_SERIAL)
    if not serial:
        return None
    return serial.decode(errors="replace")


def decode_state(message: Message) -> dict[str, Any]:
    """Translate the state and device info of a message to cloud API fields.

    The result uses the names and units of the thermostat JSON of the cloud
    API, so it can be laid over that JSON.

    Args:
    ----
        message: A message from the thermostat, or a command sent to it.

    Returns:
    -------
        The fields the message reports; empty if it has neither state nor
        device info.

    Raises:
    ------
        ValueError: The state or the device info is malformed.

    """
    fields: dict[str, Any] = {}
    state = message.message(FIELD_STATE)
    if state is not None:
        mode = state.varint(_STATE_MODE)
        if mode is not None:
            fields["RegulationMode"] = mode
        setpoints = _hold_setpoints(state)
        for hold, setpoint in setpoints.items():
            fields[_HOLD_NAMES[hold]] = _from_wire(setpoint)
        active = None if mode is None else _HOLD_FIELDS.get(mode)
        if active in setpoints:
            fields["SetPointTemp"] = _from_wire(setpoints[active])
    info = message.message(FIELD_DEVICE_INFO)
    if info is not None:
        room = info.data(_INFO_ROOM)
        if room is not None:
            fields["Room"] = room.decode(errors="replace")
        # The only place where the current setpoint of the schedule shows up.
        target = info.varint(_INFO_TARGET)
        if target is not None:
            fields["SetPointTemp"] = _from_wire(target)
    return fields


def decode_telemetry(message: Message) -> dict[str, Any]:
    """Translate the heartbeat of a message to cloud API fields.

    Args:
    ----
        message: A message from the thermostat.

    Returns:
    -------
        The floor temperature and whether the floor is heating, as far as the
        message reports them.

    Raises:
    ------
        ValueError: The heartbeat is malformed.

    """
    fields: dict[str, Any] = {}
    telemetry = message.message(FIELD_TELEMETRY)
    if telemetry is None:
        return fields
    temperature = telemetry.varint(_TELEMETRY_FLOOR)
    if temperature is not None:
        fields["Temperature"] = temperature
    heating = telemetry.varint(_TELEMETRY_HEATING)
    if heating is not None:
        fields["Heating"] = bool(heating)
    return fields


def changed_state(
    state: bytes,
    mode: int,
    temperature: int | None = None,
    moment: datetime | None = None,
) -> bytes:
    """Return the state of a thermostat with another mode and setpoint.

    The result is the thermostat's own state with a new time and the new mode.
    The setpoint of a hold goes into the field of that hold; written to the
    other hold's field, the thermostat ignores it. Everything else, such as
    the other hold's setpoint and the end time of the temporary hold, stays as
    the thermostat sent it.

    Args:
    ----
        state: The state (field 9) the thermostat sent last.
        mode: The regulation mode to switch to.
        temperature: The setpoint in 1/100 °C for a temporary or permanent
            hold. Other modes have no setpoint in the state and ignore it.
        moment: The time of the change as a timezone-aware moment; now when
            omitted.

    Returns:
    -------
        The encoded state.

    Raises:
    ------
        ValueError: The state is malformed.

    """
    changed = set_field(state, _STATE_TIME, device_time(moment))
    changed = set_field(changed, _STATE_MODE, mode)
    hold = _HOLD_FIELDS.get(mode)
    if hold is not None and temperature is not None:
        current = Message.parse(state).data(hold) or b""
        changed = set_field(
            changed, hold, set_field(current, _HOLD_SETPOINT, _to_wire(temperature))
        )
    return changed


def command(sequence: int, state: bytes, schedule: bytes | None = None) -> bytes:
    """Build the message that makes the thermostat take over a state.

    Args:
    ----
        sequence: The sequence number, which the thermostat acknowledges.
        state: The state (field 9) to take over, see changed_state().
        schedule: The schedule (field 8) the thermostat sent last, if any.

    Returns:
    -------
        The encoded message.

    """
    message = encode_field(FIELD_SEQUENCE, sequence) + encode_field(_FIELD_5, 1)
    if schedule:
        message += encode_field(FIELD_SCHEDULE, schedule)
    return message + encode_field(FIELD_STATE, state)


def response_field(message: Message) -> int | None:
    """Tell whether a message is one of the two answers that follow a login.

    Returns
    -------
        FIELD_CONFIGURATION or FIELD_SUPPORT if the message is that answer and
        nothing else, and None for any other message.

    """
    for number in (FIELD_CONFIGURATION, FIELD_SUPPORT):
        if (
            message.numbers <= {FIELD_SEQUENCE, _FIELD_5, number}
            and message.count(number) == 1
            and message.data(number) is not None
        ):
            return number
    return None


def describe(message: Message) -> str:
    """Summarise a message for debug logs.

    A login is named but never shown: it holds the account's email address.
    """
    if FIELD_LOGIN in message:
        return "login"
    parts = [
        f"seq={message.varint(FIELD_SEQUENCE)}",
        "fields=" + ",".join(str(number) for number in sorted(message.numbers)),
    ]
    kind = message.varint(FIELD_KIND)
    if kind is not None:
        parts.append(f"kind={kind}")
    try:
        parts += _describe_state(message)
        parts += _describe_device_info(message)
        parts += _describe_telemetry(message)
    except ValueError:
        parts.append("undecodable")
    return " ".join(parts)


def _from_wire(setpoint: int) -> int:
    """Convert a setpoint from the wire to 1/100 °C."""
    return round(setpoint * 100 / _WIRE_SCALE)


def _to_wire(temperature: int) -> int:
    """Convert a temperature in 1/100 °C to a setpoint on the wire."""
    return temperature * _WIRE_SCALE // 100


def _hold_setpoints(state: Message) -> dict[int, int]:
    """Return the setpoint each hold stores, by the field of the hold."""
    setpoints: dict[int, int] = {}
    for hold in (_TEMPORARY_HOLD, _PERMANENT_HOLD):
        fields = state.message(hold)
        setpoint = None if fields is None else fields.varint(_HOLD_SETPOINT)
        if setpoint is not None:
            setpoints[hold] = setpoint
    return setpoints


def _celsius(setpoint: int) -> str:
    """Show a setpoint from the wire in Celsius and Fahrenheit."""
    celsius = setpoint / _WIRE_SCALE
    return f"{celsius:.2f}C/{celsius * 9 / 5 + 32:.1f}F"


def _brief(value: int | bytes) -> str:
    """Show a field that is not interpreted."""
    if isinstance(value, int):
        return str(value)
    try:
        fields = Message.parse(value).fields
    except ValueError:
        return value.hex()
    shown = (
        f"{field.number}: "
        f"{field.value if isinstance(field.value, int) else field.value.hex()}"
        for field in fields
    )
    return "{" + ", ".join(shown) + "}"


def _describe_state(message: Message) -> list[str]:
    state = message.message(FIELD_STATE)
    if state is None:
        return []
    mode = state.varint(_STATE_MODE)
    name = "unknown" if mode is None else _MODE_NAMES.get(mode, "unknown")
    parts = [f"mode={mode} ({name})"]
    setpoints = _hold_setpoints(state)
    if _TEMPORARY_HOLD in setpoints:
        parts.append(f"temporary={_celsius(setpoints[_TEMPORARY_HOLD])}")
        hold = state.message(_TEMPORARY_HOLD)
        until = None if hold is None else hold.varint(_HOLD_END_TIME)
        if until is not None:
            end = EPOCH + timedelta(seconds=until)
            parts.append(f"until={end:%Y-%m-%d %H:%M:%SZ}")
    if _PERMANENT_HOLD in setpoints:
        parts.append(f"permanent={_celsius(setpoints[_PERMANENT_HOLD])}")
    shown = {_STATE_TIME, _STATE_MODE, _TEMPORARY_HOLD, _PERMANENT_HOLD}
    for field in sorted(state.fields, key=lambda field: field.number):
        if field.number not in shown:
            shown.add(field.number)
            parts.append(f"{FIELD_STATE}.{field.number}={_brief(field.value)}")
    return parts


def _describe_device_info(message: Message) -> list[str]:
    info = message.message(FIELD_DEVICE_INFO)
    target = None if info is None else info.varint(_INFO_TARGET)
    return [] if target is None else [f"target={_celsius(target)}"]


def _describe_telemetry(message: Message) -> list[str]:
    telemetry = message.message(FIELD_TELEMETRY)
    if telemetry is None:
        return []
    parts = []
    floor = telemetry.varint(_TELEMETRY_FLOOR)
    if floor is not None:
        # The floor temperature is in 1/100 °C, half the scale of a setpoint.
        parts.append(f"floor={_celsius(floor * 2)}")
    heating = telemetry.varint(_TELEMETRY_HEATING)
    if heating is not None:
        parts.append(f"heating={heating}")
    return parts
