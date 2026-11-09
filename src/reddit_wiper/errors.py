"""Exception types shared by the API and browser clients."""

from __future__ import annotations


class WiperError(Exception):
    """Base class for all reddit-wiper errors."""


class ConfigError(WiperError):
    """Missing or invalid configuration."""


class AuthError(WiperError):
    """Login failed or the session is no longer valid."""


class RateLimitError(WiperError):
    """Reddit asked us to slow down (HTTP 429 or a RATELIMIT API error)."""

    def __init__(self, message: str = "rate limited", retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ItemUnavailableError(WiperError):
    """The item is already deleted, removed, locked, archived or otherwise not actionable."""
