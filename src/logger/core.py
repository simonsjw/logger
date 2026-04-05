#!/usr/bin/env python3
# src/logger/core.py
"""
Core implementation of the configurable logger.

Supports rotating file handlers with gzip compression and optional
PostgreSQL logging via a pre-validated ResolvedSettingsDict from infopypg.
"""

from __future__ import (
    annotations,                                                                          # allows use of type hints before they are imported (resolved on import)
)

import asyncio
import gzip
import json
import logging
import logging.handlers
import os
from datetime import datetime
from pathlib import Path
from typing import Any

# Lazy import of infopypg components to avoid potential circular imports.
_infopypg = None
_ResolvedSettingsDict = None
_PgPoolManager = None


def _lazy_import_infopypg() -> None:
    """Perform lazy import of required infopypg components.

    This defers loading until the database path is actually used,
    improving startup performance when PostgreSQL logging is not required.
    """
    global _infopypg, _ResolvedSettingsDict, _PgPoolManager
    if _infopypg is None:
        import infopypg
        from infopypg import PgPoolManager, ResolvedSettingsDict

        _infopypg = infopypg
        _ResolvedSettingsDict = ResolvedSettingsDict
        _PgPoolManager = PgPoolManager


class GzipRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Rotating file handler that automatically compresses rotated logs with gzip."""

    def doRollover(self) -> None:
        """Perform log rollover and compress the previous file with gzip.

        Flow:
            1. Close the current stream.
            2. Rename the log file with a timestamp suffix.
            3. Compress the renamed file using gzip.
            4. Delete the uncompressed rotated file.
            5. Open a new log file for continued writing.
        """
        if self.stream:
            self.stream.close()
            self.stream = None                                                            # type: ignore

        if os.path.exists(self.baseFilename):
            # Generate unique timestamped filename for the rotated log.
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            rotated_filename = f"{self.baseFilename}.{timestamp}"

            os.rename(self.baseFilename, rotated_filename)

            # Compress using gzip and remove the uncompressed version.
            with open(rotated_filename, "rb") as f_in:
                with gzip.open(f"{rotated_filename}.gz", "wb") as f_out:
                    f_out.writelines(f_in)

            os.remove(rotated_filename)

            # Re-open for the next logging cycle.
        self.stream = self._open()                                                        # type: ignore


class Logger:
    """Configurable logger supporting file rotation and optional PostgreSQL persistence.

    The database path is only activated when a pre-validated ResolvedSettingsDict
    is supplied at construction.
    """

    def __init__(
        self,
        name: str = "app",
        log_level: int = logging.INFO,
        log_dir: str | None = None,
        max_bytes: int = 10 * 1024 * 1024,                                                # 10 MiB
        backup_count: int = 5,
        db_settings: _ResolvedSettingsDict | None = None,                                 # type: ignore
    ) -> None:
        """Initialise the Logger instance.

        Parameters
        ----------
        name : str, optional
            Name of the logger (default: "app").
        log_level : int, optional
            Logging level (default: logging.INFO).
        log_dir : str | None, optional
            Directory for log files. If None, a "logs" subdirectory
            is created in the current working directory.
        max_bytes : int, optional
            Maximum size of each log file before rotation (default: 10 MiB).
        backup_count : int, optional
            Number of rotated files to retain (default: 5).
        db_settings : _ResolvedSettingsDict | None, optional (the global set one time
            by _lazy_import_infopypg
            Pre-validated database settings from infopypg.
            If None, database logging is disabled.

        Flow
        ----
        1. Configure file paths and create log directory.
        2. Set up the standard logging.Logger with gzip rotation.
        3. Store the provided ResolvedSettingsDict for lazy pool creation.
        """
        _lazy_import_infopypg()

        self.name = name
        self.log_level = log_level
        self.max_bytes = max_bytes
        self.backup_count = backup_count

        # Resolve log directory and ensure it exists.
        if log_dir is None:
            log_dir = os.path.join(os.getcwd(), "logs")
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.log_file = self.log_dir / f"{name}.log"

        # Initialise the underlying logger and attach handler once.
        self.logger = logging.getLogger(name)
        self.logger.setLevel(log_level)

        if not self.logger.handlers:
            self._setup_file_handler()

            # Pre-validated settings (None disables DB logging).
        self._db_settings: _ResolvedSettingsDict | None = db_settings
        self._pool = None

    def _setup_file_handler(self) -> None:
        """Configure and attach the gzip-rotating file handler.

        This method is called only once during initialisation to avoid
        duplicate handlers.
        """
        handler = GzipRotatingFileHandler(
            str(self.log_file),
            maxBytes=self.max_bytes,
            backupCount=self.backup_count,
        )

        formatter = logging.Formatter(
            "%(asctime)s.%(msecs)03d | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)

    async def _ensure_db_pool(self) -> Any | None:
        """Lazily acquire the PostgreSQL connection pool.

        Returns
        -------
        Any | None
            The asyncpg pool object if db_settings was provided,
            otherwise None.

        Flow
        ----
        1. Return cached pool if already created.
        2. Skip if no settings were supplied.
        3. Perform lazy import of infopypg.
        4. Acquire pool via PgPoolManager.get_pool (pools are cached internally).
        """
        if self._pool is not None:
            return self._pool

        if not self._db_settings:
            return None

        _lazy_import_infopypg()
        assert _PgPoolManager is not None

        self._pool = await _PgPoolManager.get_pool(self._db_settings)
        return self._pool

    def info(self, message: str, extra: dict[str, Any] | None = None) -> None:
        """Log an INFO level message to the file handler only.

        Parameters
        ----------
        message : str
            The log message.
        extra : dict[str, Any] | None, optional
            Additional context dictionary passed to the logger.
        """
        self.logger.info(message, extra=extra)

    def warning(self, message: str, extra: dict[str, Any] | None = None) -> None:
        """Log an WARNING level message to the file handler only.

        Parameters
        ----------
        message : str
            The log message.
        extra : dict[str, Any] | None, optional
            Additional context dictionary passed to the logger.
        """
        self.logger.warning(message, extra=extra)

    def error(self, message: str, extra: dict[str, Any] | None = None) -> None:
        """Log an ERROR level message to the file handler only.

        Parameters
        ----------
        message : str
            The log message.
        extra : dict[str, Any] | None, optional
            Additional context dictionary passed to the logger.
        """
        self.logger.error(message, extra=extra)

    async def ainfo(self, message: str, extra: dict[str, Any] | None = None) -> None:
        """Log an INFO level message asynchronously to both file and database.

        Parameters
        ----------
        message : str
            The log message.
        extra : dict[str, Any] | None, optional
            Additional context (will be stored in the database if configured).
        """
        self.logger.info(message, extra=extra)
        await self._log_to_db("INFO", message, extra)

    async def awarning(self, message: str, extra: dict[str, Any] | None = None) -> None:
        """Log an WARNING level message asynchronously to both file and database.

        Parameters
        ----------
        message : str
            The log message.
        extra : dict[str, Any] | None, optional
            Additional context (will be stored in the database if configured).
        """
        self.logger.warning(message, extra=extra)
        await self._log_to_db("WARNING", message, extra)

    async def aerror(self, message: str, extra: dict[str, Any] | None = None) -> None:
        """Log an ERROR level message asynchronously to both file and database.

        Parameters
        ----------
        message : str
            The log message.
        extra : dict[str, Any] | None, optional
            Additional context (will be stored in the database if configured).
        """
        self.logger.error(message, extra=extra)
        await self._log_to_db("ERROR", message, extra)

    async def _log_to_db(
        self, level: str, message: str, extra: dict[str, Any] | None
    ) -> None:
        """Insert a log record into PostgreSQL if a pool is available.

        Column names match the logs table created by infopypg:
        loglvl (text), logger (text), message (text), obj (jsonb).

        We explicitly use json.dumps() + ::jsonb cast because asyncpg
        can raise "expected str, got dict" on JSONB columns in some
        environments.

        Parameters
        ----------
        level : str
            Log level string (e.g., "INFO", "ERROR").
        message : str
            The primary log message.
        extra : dict[str, Any] | None
            Optional extra context dictionary.
        """
        pool = await self._ensure_db_pool()
        if not pool:
            self.logger.error("No database pool available for logging")
            return

        try:
            await pool.execute(
                """
                INSERT INTO logs (loglvl, logger, message, obj)
                VALUES ($1, $2, $3, $4::jsonb)
                """,
                level,                                                                    # $1 → loglvl
                self.name,                                                                # $2 → logger
                message,                                                                  # $3 → message
                json.dumps(extra or {}),                                                  # $4 → obj
            )
        except Exception as e:                                                            # pylint: disable=broad-except
            # Fallback to file logging to ensure the original message is never lost.
            self.logger.error("Failed to write log to database: %s", e)


def setup_logger(
    name: str = "app",
    log_level: int = logging.INFO,
    log_dir: str | None = None,
    db_settings: _ResolvedSettingsDict | None = None,                                     # type: ignore
) -> Logger:
    """Create and return a configured Logger instance.

    This is the recommended high-level entry point for most users.

    Parameters
    ----------
    name : str, optional
        Logger name (default: "app").
    log_level : int, optional
        Logging level (default: logging.INFO).
    log_dir : str | None, optional
        Directory for log files.
    db_settings : _ResolvedSettingsDict | None, optional
        Pre-validated database settings from infopypg.

    Returns
    -------
    Logger
        Fully initialised logger ready for synchronous or asynchronous use.
    """
    return Logger(
        name=name,
        log_level=log_level,
        log_dir=log_dir,
        db_settings=db_settings,
    )


async def query_logs(
    query: str,
    db_settings: _ResolvedSettingsDict,
    params: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Execute an asynchronous query against the logs table.

    Parameters
    ----------
    query : str
        SQL query string (use $1, $2, ... placeholders for asyncpg).
    db_settings : _ResolvedSettingsDict
        Pre-validated database settings.
    params : list[Any] | None, optional
        Parameters to substitute into the query.

    Returns
    -------
    list[dict[str, Any]]
        List of result rows as dictionaries.

    Raises
    ------
    Exception
        Propagates any database errors.
    """
    _lazy_import_infopypg()
    assert _PgPoolManager is not None

    pool = await _PgPoolManager.get_pool(db_settings)

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *(params or ()))
        return [dict(row) for row in rows]

    # Alias for backward compatibility with older code that may import get_logger.


get_logger = setup_logger
