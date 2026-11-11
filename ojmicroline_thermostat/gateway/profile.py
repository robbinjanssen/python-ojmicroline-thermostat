"""The answers of OJ's device gateway that a local gateway has to repeat."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .protobuf import Message, decode_frame
from .wg4 import FIELD_CONFIGURATION, FIELD_SUPPORT, response_field

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True)
class DeviceProfile:
    """The two answers OJ's device gateway gives a thermostat after its login.

    A local gateway answers the thermostat with the same frames, so both are
    kept complete and exactly as OJ's gateway sent them. A profile belongs to
    one thermostat: another brand or firmware gets other answers.
    """

    configuration: bytes
    support: bytes

    def __post_init__(self) -> None:
        """Check that both frames are the answers they should be.

        Raises
        ------
            ValueError: A frame is malformed, or is another kind of message.

        """
        _check(self.configuration, FIELD_CONFIGURATION, "configuration")
        _check(self.support, FIELD_SUPPORT, "support")

    def to_dict(self) -> dict[str, str]:
        """Return the profile as hexadecimal strings, to store it as JSON."""
        return {
            "configuration": self.configuration.hex(),
            "support": self.support.hex(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DeviceProfile:
        """Return the profile that to_dict() stored.

        Raises
        ------
            ValueError: The data is incomplete, or is not a valid profile.

        """
        return cls(
            configuration=_from_hex(data, "configuration"),
            support=_from_hex(data, "support"),
        )


def _from_hex(data: Mapping[str, Any], name: str) -> bytes:
    try:
        return bytes.fromhex(data[name])
    except (KeyError, TypeError, ValueError) as error:
        msg = f"The stored profile has no valid {name} answer"
        raise ValueError(msg) from error


def _check(frame: bytes, field: int, name: str) -> None:
    try:
        message = Message.parse(decode_frame(frame))
    except ValueError as error:
        msg = f"The {name} answer is not a well-formed frame"
        raise ValueError(msg) from error
    if response_field(message) != field:
        msg = f"The {name} answer is another kind of message"
        raise ValueError(msg)
