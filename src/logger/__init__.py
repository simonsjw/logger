# logger/__init__.py
"""
logger package.

A flexible logging utility that supports rotating gzipped file logs and
asynchronous PostgreSQL logging. The package provides a simple setup function
and re-exports key names from the standard library logging module for
convenient one-stop imports.

Example usage
-------------
    from logger import setup_logger, query_logs, Logger, DEBUG, INFO

    logger = setup_logger("myapp", log_level=DEBUG)
    logger.info("Application started")
"""

from logging import (  # Re-export stdlib constants and types
    CRITICAL,
    DEBUG,
    ERROR,
    INFO,
    WARNING,
    Logger,
)

# Import your custom public API from core.py
# Adjust the import name if your file is actually named differently
from .core import query_logs, setup_logger  # noqa: F401

# Optional: expose the custom handlers if users need them directly
# from .core import GzipRotatingFileHandler, PostgreSQLHandler

# Define the public interface explicitly.
# This helps tools understand what is intended to be imported
# and controls behaviour of 'from logger import *'
__all__: list[str] = [
    "setup_logger",
    "query_logs",
    "DEBUG",
    "INFO",
    "WARNING",
    "CRITICAL",
    "ERROR",
    "Logger",
]
#  LocalWords:  getLogger StreamHandler
