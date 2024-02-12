"""Asynchronous Python client controlling an OJ Microline Thermostat."""


class OJMicrolineError(Exception):
    """Generic API exception."""


class OJMicrolineAuthError(OJMicrolineError):
    """API authentication/authorization exception."""


class OJMicrolineConnectionError(OJMicrolineError):
    """API connection exception."""


class OJMicrolineResultsError(OJMicrolineError):
    """API results exception."""


class OJMicrolineTimeoutError(OJMicrolineError):
    """API request timed out."""
