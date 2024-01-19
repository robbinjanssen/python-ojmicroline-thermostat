"""Asynchronous Python client controlling an OJ Microline Thermostat."""

from .api import WD5API
from .exceptions import (
    OJMicrolineAuthException,
    OJMicrolineConnectionException,
    OJMicrolineException,
    OJMicrolineResultsException,
    OJMicrolineTimeoutException,
)
from .models import Thermostat

# Backwards compatibility alias: WD5API was previously OJMicroline.
OJMicroline = WD5API

__all__ = [
    "Thermostat",
    "WD5API",
    "OJMicroline",
    "OJMicrolineException",
    "OJMicrolineAuthException",
    "OJMicrolineConnectionException",
    "OJMicrolineResultsException",
    "OJMicrolineTimeoutException",
]
