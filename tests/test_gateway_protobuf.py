"""Test the protobuf wire format of the device gateway."""

import pytest

from ojmicroline_thermostat.gateway.protobuf import (
    FIXED32,
    FIXED64,
    LENGTH_DELIMITED,
    MAX_FRAME,
    VARINT,
    Field,
    Message,
    decode_frame,
    encode_field,
    encode_frame,
    encode_varint,
    read_varint,
    set_field,
)

# A fixed-width field, which the thermostat's known messages do not use.
UNKNOWN = bytes([12 << 3 | FIXED32]) + b"abcd"


@pytest.mark.parametrize("value", [0, 1, 127, 128, 2666, 4444, 946684800, 2**63])
def test_varint_roundtrip(value: int) -> None:
    """A varint decodes to the value it was encoded from."""
    encoded = encode_varint(value)
    assert read_varint(encoded) == (value, len(encoded))


def test_read_varint_at_offset() -> None:
    """Reading starts at the offset and reports where the varint ends."""
    assert read_varint(b"\xff\x96\x01\x05", 1) == (150, 3)


@pytest.mark.parametrize("data", [b"", b"\x80", b"\xff" * 11])
def test_read_varint_rejects_bad_input(data: bytes) -> None:
    """A varint that is cut off or too long is refused."""
    with pytest.raises(ValueError, match="varint"):
        read_varint(data)


def test_encode_varint_rejects_negative() -> None:
    """A negative number has no unsigned varint."""
    with pytest.raises(ValueError, match="negative"):
        encode_varint(-1)


def test_parse_every_wire_type() -> None:
    """Fixed-width fields are parsed too, so they can be passed on."""
    data = (
        encode_field(1, 150)
        + encode_field(2, b"abc")
        + bytes([3 << 3 | FIXED32])
        + b"\x01\x02\x03\x04"
        + bytes([4 << 3 | FIXED64])
        + bytes(8)
    )
    assert Message.parse(data).fields == (
        Field(1, VARINT, 150),
        Field(2, LENGTH_DELIMITED, b"abc"),
        Field(3, FIXED32, b"\x01\x02\x03\x04"),
        Field(4, FIXED64, bytes(8)),
    )


@pytest.mark.parametrize(
    "data",
    [
        b"\x00",  # field number zero
        b"\x1b",  # wire type 3, a group
        b"\x0a\x05x",  # length-delimited, cut off
        b"\x0d\x00",  # 32 bits, cut off
        b"\x09\x00",  # 64 bits, cut off
        b"\x08",  # varint without a value
    ],
)
def test_parse_rejects_malformed_message(data: bytes) -> None:
    """A malformed message is refused instead of parsed halfway."""
    with pytest.raises(ValueError, match="protobuf"):
        Message.parse(data)


def test_message_accessors() -> None:
    """A field is read by number, and only as the type it has."""
    nested = encode_field(1, 9)
    message = Message.parse(
        encode_field(1, 7) + encode_field(2, nested) + encode_field(1, 8) + UNKNOWN
    )

    assert 1 in message
    assert 5 not in message
    assert message.numbers == {1, 2, 12}
    assert message.count(1) == 2
    assert message.count(5) == 0
    assert message.first(1) == Field(1, VARINT, 7)
    assert message.first(5) is None

    assert message.varint(1) == 7
    assert message.varint(2) is None
    assert message.varint(5) is None

    assert message.data(2) == nested
    assert message.data(1) is None
    assert message.data(5) is None
    # A fixed-width field has bytes as well, but is not length-delimited.
    assert message.data(12) is None

    assert message.message(2) == Message((Field(1, VARINT, 9),))
    assert message.message(1) is None


def test_message_rejects_malformed_nested_message() -> None:
    """A nested message is parsed as strictly as the outer one."""
    message = Message.parse(encode_field(2, b"\x00"))
    with pytest.raises(ValueError, match="protobuf"):
        message.message(2)


def test_set_field_changes_only_that_field() -> None:
    """Fields that are not understood stay as they are, byte for byte."""
    original = encode_field(1, 1234) + UNKNOWN
    assert set_field(original, 1, 5678) == encode_field(1, 5678) + UNKNOWN


def test_set_field_keeps_unusual_encodings() -> None:
    """A varint that was encoded longer than needed is not rewritten."""
    overlong = b"\x10\x80\x00"  # field 2 = 0, in two bytes
    assert Message.parse(overlong).varint(2) == 0
    original = encode_field(1, 1) + overlong
    assert set_field(original, 1, 2) == encode_field(1, 2) + overlong


def test_set_field_drops_later_occurrences() -> None:
    """A field that is set occurs once afterwards."""
    original = encode_field(1, 1) + encode_field(2, 2) + encode_field(1, 3)
    assert set_field(original, 1, 9) == encode_field(1, 9) + encode_field(2, 2)


def test_set_field_inserts_missing_field_in_order() -> None:
    """A missing field ends up where its number puts it."""
    original = encode_field(1, 1) + encode_field(5, 5)
    assert set_field(original, 3, b"x") == (
        encode_field(1, 1) + encode_field(3, b"x") + encode_field(5, 5)
    )
    assert set_field(original, 9, 9) == original + encode_field(9, 9)
    assert set_field(b"", 1, 1) == encode_field(1, 1)


def test_set_field_replaces_field_where_it_is() -> None:
    """An existing field keeps its place, also when the fields are not in order."""
    original = encode_field(5, 5) + encode_field(1, 1)
    assert set_field(original, 1, 2) == encode_field(5, 5) + encode_field(1, 2)


def test_set_field_rejects_malformed_message() -> None:
    """A field cannot be set in data that is not a message."""
    with pytest.raises(ValueError, match="protobuf"):
        set_field(b"\x00", 1, 1)


def test_frame_roundtrip() -> None:
    """A frame is a marker, the length and the message."""
    assert encode_frame(b"abc") == b"\x08\x03abc"

    message = bytes(200)
    frame = encode_frame(message)
    assert frame[:3] == b"\x08\xc8\x01"
    assert decode_frame(frame) == message

    assert decode_frame(encode_frame(b"")) == b""


@pytest.mark.parametrize(
    ("frame", "error"),
    [
        (b"", "starts with"),
        (b"\x09\x00", "starts with"),
        (b"\x08\x80", "varint"),
        (b"\x08\x03ab", "does not match"),
        (b"\x08\x01ab", "does not match"),
        (b"\x08" + encode_varint(MAX_FRAME + 1), "exceeds"),
    ],
)
def test_decode_frame_rejects_bad_frame(frame: bytes, error: str) -> None:
    """Only exactly one complete frame of a sane size is accepted."""
    with pytest.raises(ValueError, match=error):
        decode_frame(frame)
