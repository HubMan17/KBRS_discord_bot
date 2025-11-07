"""
Core modules for KBRS Discord Bot.

Provides foundational components:
- Configuration management with validation
- Centralized logging with rotation
- Custom exceptions
- Dependency injection container
"""

from core.config import settings, BotConfig
from core.logger import get_logger, setup_logging
from core.exceptions import (
    KBRSException,
    ConfigurationError,
    APIError,
    DatabaseError,
    ValidationError,
)

__all__ = [
    "settings",
    "BotConfig",
    "get_logger",
    "setup_logging",
    "KBRSException",
    "ConfigurationError",
    "APIError",
    "DatabaseError",
    "ValidationError",
]
