"""Asynchronous Python client controlling an OJ Microline Thermostat."""


class OJMicrolineException(Exception):
    """Generic API exception."""


class OJMicrolineAuthException(OJMicrolineException):
    """API authentication/authorization exception."""


class OJMicrolineConnectionException(OJMicrolineException):
    """API connection exception."""


class OJMicrolineResultsException(OJMicrolineException):
    """API results exception."""


class OJMicrolineTimeoutException(OJMicrolineException):
    """API request timed out."""
