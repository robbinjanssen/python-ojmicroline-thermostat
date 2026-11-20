"""Asynchronous Python client controlling an OJ Microline Thermostat."""

from typing import Any


class OJMicrolineError(Exception):
    """Generic API exception."""


class OJMicrolineAuthError(OJMicrolineError):
    """API authentication/authorization exception."""


class OJMicrolineConnectionError(OJMicrolineError):
    """API connection exception."""


class OJMicrolineHTTPError(OJMicrolineConnectionError):
    """The API answered with an HTTP status that tells of an error.

    It is a connection error, as it was before this class existed, with the
    status to tell a request the API refused (4xx) from an API that is out
    of order (5xx).
    """

    def __init__(self, message: str, status: int) -> None:
        """Keep the HTTP status next to the message."""
        super().__init__(message)
        self.status = status

    def __reduce__(self) -> tuple[Any, ...]:
        """Tell how to copy the error: the status is not one of its args."""
        return type(self), (*self.args, self.status)


class OJMicrolineResultsError(OJMicrolineError):
    """API results exception."""


class OJMicrolineTimeoutError(OJMicrolineError):
    """API request timed out."""


class OJMicrolineUnauthorizedError(OJMicrolineError):
    """API rejected the session or access token (HTTP 401)."""
