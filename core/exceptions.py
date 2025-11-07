"""
Custom exceptions for KBRS Bot.

Provides type-safe error handling with context preservation.
"""


class KBRSException(Exception):
    """Base exception for all KBRS bot errors."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        if self.details:
            return f"{self.message} | Details: {self.details}"
        return self.message


class ConfigurationError(KBRSException):
    """Raised when configuration is invalid or missing."""

    pass


class APIError(KBRSException):
    """Raised when external API calls fail."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_body: str | None = None,
        details: dict | None = None,
    ):
        details = details or {}
        if status_code:
            details["status_code"] = status_code
        if response_body:
            details["response_body"] = response_body
        super().__init__(message, details)
        self.status_code = status_code
        self.response_body = response_body


class DatabaseError(KBRSException):
    """Raised when database operations fail."""

    pass


class ValidationError(KBRSException):
    """Raised when input validation fails."""

    def __init__(self, message: str, field: str | None = None, details: dict | None = None):
        details = details or {}
        if field:
            details["field"] = field
        super().__init__(message, details)
        self.field = field


class TranslationError(KBRSException):
    """Raised when translation service fails."""

    pass


class RateLimitError(KBRSException):
    """Raised when rate limit is exceeded."""

    def __init__(self, message: str, retry_after: float | None = None, details: dict | None = None):
        details = details or {}
        if retry_after:
            details["retry_after"] = retry_after
        super().__init__(message, details)
        self.retry_after = retry_after


class AuthenticationError(APIError):
    """Raised when authentication fails."""

    pass


class ResourceNotFoundError(KBRSException):
    """Raised when requested resource is not found."""

    pass
