"""Fixtures shared by the tests of the device gateway."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from .gateway_fakes import Events, FakeThermostat, issue_certificate

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path

    from ojmicroline_thermostat.gateway import WG4Gateway


@pytest.fixture(scope="session", name="certificate")
def certificate_fixture(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Create the certificate and key that the user of a gateway supplies."""
    directory = tmp_path_factory.mktemp("gateway")
    certfile, keyfile, _ = issue_certificate(directory, "gateway")
    return certfile, keyfile


@pytest.fixture(name="events")
def events_fixture() -> Events:
    """Collect what the gateway reports about its thermostats."""
    return []


@pytest.fixture(name="connect")
async def connect_fixture(
    gateway: WG4Gateway,
) -> AsyncIterator[Callable[[], Any]]:
    """Connect fake thermostats to the gateway of a test, and hang up after it."""
    thermostats: list[FakeThermostat] = []

    async def connect() -> FakeThermostat:
        thermostat = await FakeThermostat.connect(gateway.port)
        thermostats.append(thermostat)
        return thermostat

    yield connect
    for thermostat in thermostats:
        await thermostat.close()
