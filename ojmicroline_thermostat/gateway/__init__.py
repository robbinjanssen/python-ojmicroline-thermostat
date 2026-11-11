"""Local device gateway for WG4-series thermostats.

A WG4 thermostat keeps a connection open to OJ's device gateway. When a DNS
record on the local network points that gateway's hostname at this machine,
the thermostat connects here instead. This package holds what a local gateway
needs to speak the thermostat's protocol.

The protocol handling is adapted from peskyAdmin/schluter-local (MIT), see the
LICENSE file in this package.
"""

from .profile import DeviceProfile
from .server import (
    WG4_NORTH_AMERICA,
    GatewayEndpoint,
    GatewayMode,
    ProfileListener,
    StateListener,
    UpstreamListener,
    UpstreamStatus,
    WG4Gateway,
    server_ssl_context,
)

__all__ = [
    "WG4_NORTH_AMERICA",
    "DeviceProfile",
    "GatewayEndpoint",
    "GatewayMode",
    "ProfileListener",
    "StateListener",
    "UpstreamListener",
    "UpstreamStatus",
    "WG4Gateway",
    "server_ssl_context",
]
