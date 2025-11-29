#!/usr/bin/env python3

"""
Configurable logging module with rotating file handlers and gzip compression.

This module provides a setup function for initialising a logger that writes to
rotating files with custom timestamp formatting (including microseconds). It
supports configuration via environment variables loaded from a .env file (using
python-dotenv), falling back to defaults if not set. Use this for applications
requiring precise, compressed, and size-managed logging without database
integration.

Notes
-----
- Environment variables are optional but override defaults for flexibility.
- LOG_FILE_PATH determines the log file location; defaults to a 'logs' directory
  relative to this module's parent.
- Rotation compresses old logs to .gz format to save space.
- Designed for use in the 'grok' conda environment; assumes python-dotenv is
  installed.

Parameters
----------
None (module-level; see setup_logger for function parameters)

Returns
-------
None (exposes setup_logger function for logger configuration)

Raises
------
None (errors are handled within setup_logger, e.g., via os.getenv fallbacks)

Examples
--------
# Log to file
>>> import logger
>>> log = logger.setup_logger()
>>> log.info("Application started")

# Log to Postgres
from logger import ResolvedSettingsDict, setup_logger

# Define connection settings (replace with your actual DB details)
settings: ResolvedSettingsDict = {
    "DB_USER": "your_username",
    "DB_HOST": "localhost",
    "DB_PORT": "5432",
    "DB_NAME": "your_database",
    "PASSWORD": "your_password",  
    "TABLESPACE_NAME": "your_tablespace",
    "TABLESPACE_PATH": None,
    "EXTENSIONS": None,
}

# Setup logger with Postgres configuration
logger = setup_logger(log_location=settings, log_level=10)  # DEBUG level

# Emit sample logs
logger.debug("Debug message for testing.")
logger.info("Info message with object.", extra={"obj": {"user_id": 123, "action": "login"}})
logger.warning("Warning: Potential issue detected.")
logger.error("Error occurred during processing.")
"""

import asyncio
import gzip
import json
import logging
import os
import shutil
from datetime import datetime
from logging import Formatter, LogRecord, Logger
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TypedDict, override

import asyncpg
from dotenv import load_dotenv

# Load environment variables from .env if present
_ = load_dotenv()


class ResolvedSettingsDict(TypedDict):
    """
    Typed dictionary for resolved settings after validation, ensuring all str except
    extensions.

    This narrows types post-checks (e.g., PASSWORD guaranteed str), for precise
    inference.
    """
    DB_USER: str
    DB_HOST: str
    DB_PORT: str
    DB_NAME: str
    PASSWORD: str | None
    TABLESPACE_NAME: str
    TABLESPACE_PATH: str | None
    EXTENSIONS: list[str] | None


class CustomFormatter(Formatter):
    """
    Custom formatter to ensure timestamps include microseconds.

    Parameters
    ----------
    fmt : str, optional
        Format string for the log message.
    datefmt : str, optional
        Date format string for timestamps.

    Returns
    -------
    Formatter
        Instance configured for custom timestamp formatting.
    """

    @override
    def formatTime(self, record: LogRecord, datefmt: str | None = None) -> str:
        """
        Override to format timestamps with microseconds.

        Parameters
        ----------
        record : LogRecord
            The log record being formatted.
        datefmt : str, optional
            Custom date format string.

        Returns
        -------
        str
            Formatted timestamp string.
        """
        created_time = datetime.fromtimestamp(record.created)
        if datefmt:
            return created_time.strftime(datefmt)
        return created_time.strftime("%Y-%m-%d %H:%M:%S.%f")


class PostgresHandler(logging.Handler):
    """
    Custom logging handler for inserting logs into a PostgreSQL table.

    Uses asyncpg for asynchronous inserts via a connection pool. Assumes the 'log'
    table exists with columns: datetime (timestamp), level (text), app (text),
    name (text), message (text), obj (jsonb). Connection is established lazily
    on init.

    Notes
    -----
    - 'app' uses record.name (logger name); 'name' uses record.funcName.
    - 'obj' pulls from record extras if present (e.g., extra={'obj': dict}),
      serialised to JSON; else None.
    - Pool is created synchronously via asyncio.run_until_complete for simplicity
      in sync contexts.
    - Only uses connection params from settings; ignores TABLESPACE/EXTENSIONS.

    Parameters
    ----------
    settings : ResolvedSettingsDict
        Dictionary with DB connection details.

    Returns
    -------
    None

    Raises
    ------
    RuntimeError
        If pool creation or insert fails (e.g., connection refused); check
        settings and DB availability.
    """

    def __init__(self, settings: ResolvedSettingsDict) -> None:
        super().__init__()
        self.settings = settings
        self.pool: asyncpg.Pool | None = None
        loop = asyncio.get_event_loop()
        self.pool = loop.run_until_complete(self._create_pool())

    async def _create_pool(self) -> asyncpg.Pool:
        """
        Asynchronous pool creation helper.

        Returns
        -------
        asyncpg.Pool
            Configured connection pool.
        """
        user = self.settings["DB_USER"]
        host = self.settings["DB_HOST"]
        port = self.settings["DB_PORT"]
        db = self.settings["DB_NAME"]
        password = self.settings.get("PASSWORD")

        dsn = f"postgresql://{user}@{host}:{port}/{db}"
        if password:
            dsn = f"postgresql://{user}:{password}@{host}:{port}/{db}"

        return await asyncpg.create_pool(dsn, min_size=1, max_size=10)

    def emit(self, record: LogRecord) -> None:
        """
        Synchronous emit wrapper; delegates to async_emit.

        Parameters
        ----------
        record : LogRecord
            The log record to emit.
        """
        if self.pool is None:
            raise RuntimeError("Pool not initialised")
        loop = asyncio.get_event_loop()
        loop.run_until_complete(self._async_emit(record))

    async def _async_emit(self, record: LogRecord) -> None:
        """
        Asynchronous insert of log record into DB.

        Parameters
        ----------
        record : LogRecord
            The log record to insert.
        """
        dt = datetime.fromtimestamp(record.created)
        level = record.levelname
        app = record.name
        name = record.funcName
        message = record.getMessage()
        obj = json.dumps(getattr(record, "obj", None)) if hasattr(record, "obj") else None

        query = """
            INSERT INTO log (datetime, level, app, name, message, obj)
            VALUES ($1, $2, $3, $4, $5, $6)
        """

        async with self.pool.acquire(timeout=10) as conn:  # type: asyncpg.Connection
            await conn.execute(query, dt, level, app, name, message, obj)

    def close(self) -> None:
        """
        Close the pool on handler shutdown.
        """
        if self.pool:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(self.pool.close())
        super().close()


def namer(name: str) -> str:
    """
    Rename log file for rotation by appending .gz extension.

    Parameters
    ----------
    name : str
        Original log file name.

    Returns
    -------
    str
        New name with .gz extension.
    """
    return name + ".gz"


def rotator(source: str | Path, dest: str | Path) -> None:
    """
    Rotate log file by compressing it to gzip and removing the original.

    Notes
    -----
    Uses shutil.copyfileobj for efficient streaming without loading full file into memory.

    Parameters
    ----------
    source : str or Path
        Path to the source log file.
    dest : str or Path
        Path to the destination gzipped file.

    Raises
    ------
    OSError
        If file operations fail (e.g., permissions); check file paths and access rights.
    """
    with open(source, "rb") as f_in:
        with gzip.open(dest, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    os.remove(source)


def setup_logger(
    logger_name: str | None = None,
    log_location: str | ResolvedSettingsDict | None = None,
    log_file_maximum_size: int | None = None,
    backup_count: int | None = None,
    log_level: int | None = None,
) -> Logger:
    """
    Set up logging to a rotating file or PostgreSQL table with custom handling.

    If LOG_LOCATION is a ResolvedSettingsDict, prefers PostgreSQL logging (using
    asyncpg pool for inserts). Otherwise, falls back to file-based with rotation.
    If parameters are not provided, uses environment variables or defaults.
    Avoids duplicate handlers by checking existing ones.

    Notes
    -----
    - For file: Log directory created relative to this module if not specified.
    - For Postgres: Assumes 'log' table exists; inserts separate fields without
      rotation params.
    - Timestamps include microseconds for precision in both modes.
    - 'obj' requires extra={'obj': dict} in log calls for JSONB.

    Parameters
    ----------
    logger_name : str, optional
        Name for the logger; uses root logger if None.
    log_location : str or ResolvedSettingsDict, optional
        Path to log file or DB settings dict; defaults to env LOG_FILE_PATH or
        '../logs/app.log'.
    log_file_maximum_size : int, optional
        Max log size in bytes before rotation (file only); defaults to env
        LOG_MAX_SIZE or 10MB.
    backup_count : int, optional
        Number of backups to keep (file only); defaults to env LOG_BACKUP_COUNT
        or 10.
    log_level : int, optional
        Logging level; defaults to env LOG_LEVEL or DEBUG.

    Returns
    -------
    Logger
        Configured logger instance.

    Raises
    ------
    ValueError
        If log_level_str from env is invalid; use a valid level like 'DEBUG'.
    OSError
        If log directory creation or file access fails (file mode); ensure write
        permissions.
    RuntimeError
        If Postgres pool creation fails; check DB settings and availability.

    Examples
    --------
    >>> from logger import setup_logger, ResolvedSettingsDict
    >>> settings: ResolvedSettingsDict = {"DB_USER": "user", "DB_HOST": "localhost", ...}
    >>> logger = setup_logger(log_location=settings)
    >>> logger.info("Test message", extra={"obj": {"key": "value"}})
    """
    if not log_level:
        log_level_str = os.getenv("LOG_LEVEL", "DEBUG").upper()
        log_level = getattr(logging, log_level_str, logging.DEBUG)

    logger: Logger = logging.getLogger(logger_name) if logger_name else logging.getLogger()
    if log_level: 
        logger.setLevel(log_level)

    if not logger.handlers:
        if isinstance(log_location, dict):
            # Postgres mode
            handler = PostgresHandler(log_location)
            # No formatter needed; fields inserted directly
        else:
            # File mode (str or None)
            if not log_location:
                script_dir = Path(__file__).resolve().parent
                log_dir = script_dir.parent / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                log_location = os.getenv("LOG_FILE_PATH", str(log_dir / "app.log"))

            if not log_file_maximum_size:
                log_file_maximum_size = int(os.getenv("LOG_MAX_SIZE", "10485760"))  # 10 MB

            if not backup_count:
                backup_count = int(os.getenv("LOG_BACKUP_COUNT", "10"))

            handler = RotatingFileHandler(
                filename=log_location,
                maxBytes=log_file_maximum_size,
                backupCount=backup_count,
            )
            handler.rotator = rotator
            handler.namer = namer

            log_format = "[%(asctime)s; %(levelname)s; %(funcName)s] %(message)s; %(name)s;"
            formatter = CustomFormatter(
                fmt=log_format, datefmt="%Y-%m-%d %H:%M:%S.%f"
            )
            handler.setFormatter(formatter)

        logger.addHandler(handler)

        logger.info("Logger initialised.")

    return logger

#  LocalWords:  tablespace fallbacks
