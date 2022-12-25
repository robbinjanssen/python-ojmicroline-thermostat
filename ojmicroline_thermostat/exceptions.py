"""Asynchronous Python client providing Open Data information of Amsterdam."""


class OJMicrolineError(Exception):
    """Generic API exception."""


class OJMicrolineConnectionError(OJMicrolineError):
    """API connection exception."""


class OJMicrolineResultsError(OJMicrolineError):
    """API results exception."""
