"""
Centralized logging with rotation and formatting.

Features:
- Automatic file rotation (10MB max, 5 backups)
- Colored console output
- Structured logging with context
- Performance monitoring decorators
"""

import logging
import sys
import time
import functools
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Any, Callable, TypeVar
from datetime import datetime

# Type hints
F = TypeVar("F", bound=Callable[..., Any])

# Logging format
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Color codes for console
COLORS = {
    "DEBUG": "\033[36m",     # Cyan
    "INFO": "\033[32m",      # Green
    "WARNING": "\033[33m",   # Yellow
    "ERROR": "\033[31m",     # Red
    "CRITICAL": "\033[35m",  # Magenta
    "RESET": "\033[0m",      # Reset
}


class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors for console output."""

    def format(self, record: logging.LogRecord) -> str:
        levelname = record.levelname
        if levelname in COLORS:
            record.levelname = f"{COLORS[levelname]}{levelname}{COLORS['RESET']}"
        return super().format(record)


def setup_logging(
    log_dir: Path | None = None,
    level: str = "INFO",
    console: bool = True,
    file: bool = True,
) -> None:
    """
    Configure centralized logging for the application.

    Args:
        log_dir: Directory for log files (default: logs/)
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        console: Enable console output
        file: Enable file output
    """
    log_dir = log_dir or Path("logs")
    log_dir.mkdir(exist_ok=True)

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))

    # Remove existing handlers
    root_logger.handlers.clear()

    # Console handler with colors
    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(ColoredFormatter(LOG_FORMAT, DATE_FORMAT))
        root_logger.addHandler(console_handler)

    # File handler with rotation
    if file:
        log_file = log_dir / f"kbrs_bot_{datetime.now().strftime('%Y%m%d')}.log"
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        root_logger.addHandler(file_handler)

    # Error file handler (separate file for errors)
    if file:
        error_file = log_dir / f"kbrs_errors_{datetime.now().strftime('%Y%m%d')}.log"
        error_handler = RotatingFileHandler(
            error_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        root_logger.addHandler(error_handler)

    # Silence noisy libraries
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
    logging.getLogger("telebot").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)

    root_logger.info("Logging system initialized")


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance with the specified name.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Configured logger instance
    """
    return logging.getLogger(name)


def log_execution_time(func: F) -> F:
    """
    Decorator to log function execution time.

    Usage:
        @log_execution_time
        async def my_function():
            ...
    """
    logger = get_logger(func.__module__)

    @functools.wraps(func)
    async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        try:
            result = await func(*args, **kwargs)
            elapsed = time.perf_counter() - start
            logger.debug(f"{func.__name__} completed in {elapsed:.3f}s")
            return result
        except Exception as e:
            elapsed = time.perf_counter() - start
            logger.error(f"{func.__name__} failed after {elapsed:.3f}s: {e}")
            raise

    @functools.wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        try:
            result = func(*args, **kwargs)
            elapsed = time.perf_counter() - start
            logger.debug(f"{func.__name__} completed in {elapsed:.3f}s")
            return result
        except Exception as e:
            elapsed = time.perf_counter() - start
            logger.error(f"{func.__name__} failed after {elapsed:.3f}s: {e}")
            raise

    # Return appropriate wrapper based on function type
    if asyncio.iscoroutinefunction(func):
        return async_wrapper  # type: ignore
    return sync_wrapper  # type: ignore


def log_errors(func: F) -> F:
    """
    Decorator to log unhandled exceptions.

    Usage:
        @log_errors
        async def my_function():
            ...
    """
    logger = get_logger(func.__module__)

    @functools.wraps(func)
    async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.exception(f"Unhandled exception in {func.__name__}: {e}")
            raise

    @functools.wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.exception(f"Unhandled exception in {func.__name__}: {e}")
            raise

    if asyncio.iscoroutinefunction(func):
        return async_wrapper  # type: ignore
    return sync_wrapper  # type: ignore


# Import asyncio for type checking
import asyncio
