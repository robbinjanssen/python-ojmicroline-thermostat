"""Local device gateway for WG4-series and WD5-series thermostats.

A thermostat keeps a connection open to OJ's device gateway. When a DNS record
on the local network points that gateway's hostname at this machine, the
thermostat connects here instead. This package holds what a local gateway
needs to speak the thermostat's protocol.

The connection handling and the WG4 protocol are adapted from
peskyAdmin/schluter-local, the WD5 protocol from
patricknitsch/ioBroker.schlueter-thermostat (both MIT), see the LICENSE file
in this package.
"""

from .profile import DeviceProfile
from .server import (
    WD5_R1099,
    WD5_SL003,
    WG4_NORTH_AMERICA,
    DeviceGateway,
    GatewayEndpoint,
    GatewayMode,
    StateListener,
    UpstreamListener,
    UpstreamStatus,
    server_ssl_context,
)
from .wd5_gateway import WD5Gateway
from .wg4_gateway import ProfileListener, WG4Gateway

__all__ = [
    "WD5_R1099",
    "WD5_SL003",
    "WG4_NORTH_AMERICA",
    "DeviceGateway",
    "DeviceProfile",
    "GatewayEndpoint",
    "GatewayMode",
    "ProfileListener",
    "StateListener",
    "UpstreamListener",
    "UpstreamStatus",
    "WD5Gateway",
    "WG4Gateway",
    "server_ssl_context",
]
