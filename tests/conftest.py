"""Fixtures shared by the tests of the device gateway."""

from __future__ import annotations

import ssl
from typing import TYPE_CHECKING, Any

import pytest

from .gateway_fakes import Events, FakeCloud, FakeThermostat, issue_certificate

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path

    from ojmicroline_thermostat.gateway import DeviceGateway


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


@pytest.fixture(scope="session", name="cloud_certificate")
def cloud_certificate_fixture(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[ssl.SSLContext, str]:
    """Create the certificate of the fake OJ gateway, and its fingerprint."""
    directory = tmp_path_factory.mktemp("cloud")
    certfile, keyfile, fingerprint = issue_certificate(directory, "cloud")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile, keyfile)
    return context, fingerprint


@pytest.fixture(name="cloud")
async def cloud_fixture(
    cloud_certificate: tuple[ssl.SSLContext, str],
) -> AsyncIterator[FakeCloud]:
    """Run a fake OJ gateway on a free port."""
    cloud = FakeCloud(*cloud_certificate)
    await cloud.start()
    yield cloud
    await cloud.stop()


@pytest.fixture(name="connect")
async def connect_fixture(
    gateway: DeviceGateway,
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
