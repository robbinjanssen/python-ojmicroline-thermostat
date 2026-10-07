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


class OJMicrolineUnauthorizedError(OJMicrolineError):
    """API rejected the session or access token (HTTP 401)."""
