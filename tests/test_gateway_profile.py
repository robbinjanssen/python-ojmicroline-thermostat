"""Test the profile of a thermostat for the device gateway."""

from typing import Any

import pytest

from ojmicroline_thermostat.gateway import DeviceProfile
from ojmicroline_thermostat.gateway.protobuf import encode_field, encode_frame

# Frames shaped like the two answers of OJ's gateway, with made-up content.
CONFIGURATION = encode_frame(
    encode_field(1, 1) + encode_field(5, 1) + encode_field(10, encode_field(1, 1))
)
SUPPORT = encode_frame(
    encode_field(1, 0) + encode_field(5, 1) + encode_field(18, b"Example support")
)


def test_profile_roundtrip() -> None:
    """A profile survives being stored as JSON."""
    profile = DeviceProfile(configuration=CONFIGURATION, support=SUPPORT)
    stored = profile.to_dict()

    assert stored == {
        "configuration": CONFIGURATION.hex(),
        "support": SUPPORT.hex(),
    }
    assert DeviceProfile.from_dict(stored) == profile


def test_profile_compares_by_content() -> None:
    """Two recordings of the same answers are the same profile."""
    profile = DeviceProfile(configuration=CONFIGURATION, support=SUPPORT)
    other = encode_frame(encode_field(18, b"Other support"))

    assert profile == DeviceProfile(configuration=CONFIGURATION, support=SUPPORT)
    assert hash(profile) == hash(
        DeviceProfile(configuration=CONFIGURATION, support=SUPPORT)
    )
    assert profile != DeviceProfile(configuration=CONFIGURATION, support=other)


@pytest.mark.parametrize(
    ("configuration", "support", "error"),
    [
        # Not a frame: the marker and length are missing.
        (encode_field(10, b"x"), SUPPORT, "configuration answer is not a well-formed"),
        # A frame whose content is not a message.
        (CONFIGURATION, encode_frame(b"\x00"), "support answer is not a well-formed"),
        # The answers the wrong way around.
        (SUPPORT, CONFIGURATION, "configuration answer is another kind"),
        # A login is never stored as a profile.
        (
            CONFIGURATION,
            encode_frame(encode_field(6, b"x") + encode_field(18, b"x")),
            "support answer is another kind",
        ),
        (b"", SUPPORT, "configuration answer is not a well-formed"),
    ],
)
def test_profile_rejects_other_frames(
    configuration: bytes, support: bytes, error: str
) -> None:
    """Only the two answers themselves can be stored and repeated."""
    with pytest.raises(ValueError, match=error):
        DeviceProfile(configuration=configuration, support=support)


@pytest.mark.parametrize(
    ("data", "error"),
    [
        ({}, "no valid configuration answer"),
        ({"configuration": CONFIGURATION.hex()}, "no valid support answer"),
        (
            {"configuration": CONFIGURATION.hex(), "support": "not hexadecimal"},
            "no valid support answer",
        ),
        ({"configuration": 5, "support": SUPPORT.hex()}, "no valid configuration"),
        (None, "no valid configuration answer"),
        (
            {"configuration": SUPPORT.hex(), "support": CONFIGURATION.hex()},
            "another kind",
        ),
    ],
)
def test_profile_from_dict_rejects_bad_data(data: Any, error: str) -> None:
    """Stored data that is damaged or incomplete is refused."""
    with pytest.raises(ValueError, match=error):
        DeviceProfile.from_dict(data)
