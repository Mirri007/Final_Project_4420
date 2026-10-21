"""Custom exception hierarchy.

Every error the application raises on purpose derives from BusBidderError. The CLI catches
that one base class, shows a clear message and exits with a non-zero status, while genuine
programming errors still surface as ordinary tracebacks.
"""


class BusBidderError(Exception):
    """Base class for all expected application errors."""


class ConfigError(BusBidderError):
    """The settings file is missing, malformed or contains invalid values."""


class DataFileError(BusBidderError):
    """A source file is missing, unreadable or structurally wrong (e.g. missing columns)."""


class RecordValidationError(BusBidderError):
    """One field of one record failed validation."""

    def __init__(self, field: str, message: str):
        self.field = field
        super().__init__(f"{field}: {message}")


class InvalidRequestError(BusBidderError):
    """The traveller's request is inconsistent or impossible."""


class InvalidWeightsError(BusBidderError):
    """Criterion weights are unknown, negative or do not total 100%."""


class ProfileError(BusBidderError):
    """A traveller profile is missing or incorrectly defined."""


class BookingError(BusBidderError):
    """A booking could not be created, found or changed."""


class SeatsUnavailableError(BookingError):
    """The offer no longer has enough free seats."""


class BookingNotFoundError(BookingError):
    """No booking exists with the given reference."""


class InvalidReferenceError(BookingError):
    """A booking reference does not have the expected format."""
