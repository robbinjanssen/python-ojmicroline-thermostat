"""Protobuf wire format, as far as the device gateway needs it.

The thermostats speak protobuf without a published schema, so messages are
handled by field number. A message is parsed into its fields, and a field is
changed in the encoded message itself: everything the thermostat sent that
this library does not understand goes back to it byte for byte.

Adapted from peskyAdmin/schluter-local (MIT), see the LICENSE file in this
package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Iterator

VARINT = 0
FIXED64 = 1
LENGTH_DELIMITED = 2
FIXED32 = 5

# A frame is this byte, the length of the message as a varint, and the message.
FRAME_MARKER = 0x08
# Real frames stay under 1 KiB; refuse anything absurd.
MAX_FRAME = 64 * 1024

_MAX_VARINT_BYTES = 10
_FIXED_SIZES = {FIXED32: 4, FIXED64: 8}


def read_varint(data: bytes, offset: int = 0) -> tuple[int, int]:
    """Read the unsigned varint at an offset.

    Args:
    ----
        data: The encoded data.
        offset: Where the varint starts.

    Returns:
    -------
        The value, and the offset of the byte after the varint.

    Raises:
    ------
        ValueError: The varint is cut off, or longer than ten bytes.

    """
    value = 0
    for index in range(_MAX_VARINT_BYTES):
        position = offset + index
        if position >= len(data):
            msg = "Truncated protobuf varint"
            raise ValueError(msg)
        byte = data[position]
        value |= (byte & 0x7F) << (7 * index)
        if not byte & 0x80:
            return value, position + 1
    msg = "Oversized protobuf varint"
    raise ValueError(msg)


def encode_varint(value: int) -> bytes:
    """Encode an unsigned varint.

    Raises
    ------
        ValueError: The value is negative.

    """
    if value < 0:
        msg = "A protobuf varint cannot be negative"
        raise ValueError(msg)
    encoded = bytearray()
    while value > 0x7F:
        encoded.append(value & 0x7F | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


class Field(NamedTuple):
    """One field of a message.

    The value is the number of a varint field, and the content of any other
    field.
    """

    number: int
    wire_type: int
    value: int | bytes


@dataclass(frozen=True)
class Message:
    """A parsed message: its fields, in the order they were sent."""

    fields: tuple[Field, ...]

    @classmethod
    def parse(cls, data: bytes) -> Message:
        """Parse an encoded message.

        Raises
        ------
            ValueError: The data is not a well-formed message.

        """
        return cls(tuple(field for field, _, _ in _scan(data)))

    def __contains__(self, number: object) -> bool:
        """Return whether the message has a field with this number."""
        return any(field.number == number for field in self.fields)

    @property
    def numbers(self) -> frozenset[int]:
        """Return the numbers of the fields in the message."""
        return frozenset(field.number for field in self.fields)

    def count(self, number: int) -> int:
        """Return how often a field occurs."""
        return sum(field.number == number for field in self.fields)

    def first(self, number: int) -> Field | None:
        """Return the first occurrence of a field, or None without one."""
        return next((field for field in self.fields if field.number == number), None)

    def varint(self, number: int) -> int | None:
        """Return the value of a field, if its first occurrence is a varint."""
        field = self.first(number)
        if field is None or not isinstance(field.value, int):
            return None
        return field.value

    def data(self, number: int) -> bytes | None:
        """Return a field's content, if its first occurrence is length-delimited."""
        field = self.first(number)
        if (
            field is None
            or field.wire_type != LENGTH_DELIMITED
            or not isinstance(field.value, bytes)
        ):
            return None
        return field.value

    def message(self, number: int) -> Message | None:
        """Parse the content of a length-delimited field as a nested message.

        Raises
        ------
            ValueError: The content is not a well-formed message.

        """
        data = self.data(number)
        return None if data is None else Message.parse(data)


def _scan(data: bytes) -> Iterator[tuple[Field, int, int]]:
    """Yield every field of a message with the offsets where it starts and ends."""
    offset = 0
    while offset < len(data):
        start = offset
        tag, offset = read_varint(data, offset)
        number, wire_type = tag >> 3, tag & 7
        if not number:
            msg = "Invalid protobuf field number zero"
            raise ValueError(msg)
        value: int | bytes
        if wire_type == VARINT:
            value, offset = read_varint(data, offset)
        elif wire_type == LENGTH_DELIMITED:
            length, offset = read_varint(data, offset)
            value = data[offset : offset + length]
            offset += length
        elif wire_type in _FIXED_SIZES:
            value = data[offset : offset + _FIXED_SIZES[wire_type]]
            offset += _FIXED_SIZES[wire_type]
        else:
            msg = f"Unsupported protobuf wire type {wire_type}"
            raise ValueError(msg)
        if offset > len(data):
            msg = "Truncated protobuf field"
            raise ValueError(msg)
        yield Field(number, wire_type, value), start, offset


def encode_field(number: int, value: int | bytes) -> bytes:
    """Encode a number as a varint field, or bytes as a length-delimited field."""
    if isinstance(value, int):
        return encode_varint(number << 3 | VARINT) + encode_varint(value)
    return (
        encode_varint(number << 3 | LENGTH_DELIMITED)
        + encode_varint(len(value))
        + value
    )


def set_field(data: bytes, number: int, value: int | bytes) -> bytes:
    """Return an encoded message with one field set and every other byte kept.

    The first occurrence of the field is replaced, and later ones are dropped.
    A field that was missing is inserted before the first field with a higher
    number, so a message with ascending field numbers stays ascending.

    Args:
    ----
        data: The encoded message.
        number: The number of the field to set.
        value: A number for a varint field, or bytes for a length-delimited one.

    Returns:
    -------
        The encoded message with the field set.

    Raises:
    ------
        ValueError: The data is not a well-formed message.

    """
    encoded = encode_field(number, value)
    spans = list(_scan(data))
    present = any(field.number == number for field, _, _ in spans)
    chunks: list[bytes] = []
    placed = False
    for field, start, end in spans:
        if field.number == number:
            if not placed:
                chunks.append(encoded)
                placed = True
            continue
        if not present and not placed and field.number > number:
            chunks.append(encoded)
            placed = True
        chunks.append(data[start:end])
    if not placed:
        chunks.append(encoded)
    return b"".join(chunks)


def encode_frame(message: bytes) -> bytes:
    """Wrap an encoded message in a frame."""
    return bytes([FRAME_MARKER]) + encode_varint(len(message)) + message


def decode_frame(frame: bytes) -> bytes:
    """Return the encoded message of one complete frame.

    Raises
    ------
        ValueError: The data is not exactly one frame, or the frame is too large.

    """
    if not frame or frame[0] != FRAME_MARKER:
        msg = f"A frame starts with {FRAME_MARKER:#04x}"
        raise ValueError(msg)
    size, offset = read_varint(frame, 1)
    if size > MAX_FRAME:
        msg = f"A frame of {size} bytes exceeds the limit of {MAX_FRAME}"
        raise ValueError(msg)
    if size != len(frame) - offset:
        msg = "The length of the frame does not match its content"
        raise ValueError(msg)
    return frame[offset:]


def split_frames(data: bytes) -> list[bytes]:
    """Return the encoded messages of one or more complete frames in a row.

    Raises
    ------
        ValueError: The data is not a row of complete frames.

    """
    messages = []
    offset = 0
    while offset < len(data):
        if data[offset] != FRAME_MARKER:
            msg = f"A frame starts with {FRAME_MARKER:#04x}"
            raise ValueError(msg)
        size, start = read_varint(data, offset + 1)
        end = start + size
        if size > MAX_FRAME or end > len(data):
            msg = "The length of a frame does not match its content"
            raise ValueError(msg)
        messages.append(data[start:end])
        offset = end
    return messages


# Both series send a setpoint in 1/200 °C.
SETPOINT_SCALE = 200


def from_wire(setpoint: int) -> int:
    """Convert a setpoint from the wire to 1/100 °C."""
    return round(setpoint * 100 / SETPOINT_SCALE)


def to_wire(temperature: int) -> int:
    """Convert a temperature in 1/100 °C to a setpoint on the wire."""
    return temperature * SETPOINT_SCALE // 100


def brief(value: int | bytes) -> str:
    """Show a field that is not interpreted, for a log."""
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
