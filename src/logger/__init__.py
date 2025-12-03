#!/usr/bin/env python3

"""
Configurable logging module with rotating file handlers and gzip compression, or PostgreSQL integration via pyPg.

This module provides a setup function for initialising a logger that writes to
rotating files with custom timestamp formatting (including microseconds) or to a PostgreSQL database
using the shared connection pool from pyPg.pgPool. It supports configuration via environment variables
loaded from a .env file (using python-dotenv), falling back to defaults if not set. Use this for applications
requiring precise, compressed, and size-managed logging, with optional database integration via infopypg.

Notes
-----
- Environment variables are optional but override defaults for flexibility.
- LOG_FILE_PATH determines the log file location; defaults to a 'logs' directory
  relative to this module's parent.
- Rotation compresses old logs to .gz format to save space.
- For PostgreSQL logging, uses infopypg's shared pgpool; creates 'logs' table if
  missing.
- Designed for use in the 'grok' conda environment;
  assumes installation of:
  * python-dotenv
  * infopypg
- logger does not require the existence of an psql connection when imported even
  if logger is subsequently used to define a logger to psql
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

# Log to Postgres (using pyPg settings)
from pyPg.postgres_types import ResolvedSettingsDict
from logger import setup_logger

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
from typing import TypedDict, override, cast

from infopypg import ResolvedSettingsDict

from dotenv import load_dotenv

# Load environment variables from .env if present
_ = load_dotenv()


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

    Uses the shared asyncpg pool from infopypg.pgpool for asynchronous inserts. Assumes the 'logs'
    table exists with columns: tStamp (timestamp with time zone), loglvl (text), logger (text),
    message (text), obj (jsonb). Pool is accessed from pgPool.instance().

    Notes
    -----
    - 'logger' uses record.name.
    - 'obj' pulls from record extras if present (e.g., extra={'obj': dict}),
      serialised to JSON; else None.
    - Assumes pgpool is initialised (e.g., via setup_logger); uses shared pool.
    - Only performs inserts; no pool creation here.

    Parameters
    ----------
    None (uses shared pgpool)

    Returns
    -------
    None

    Raises
    ------
    RuntimeError
        If pgpool not initialised or insert fails (e.g., connection issues).
    """

    def __init__(self) -> None:
        super().__init__()
        import asyncpg  # Lazy import to avoid cycles
        from infopypg.pgpool import pgpool  # Lazy import
        loop = asyncio.get_event_loop()
        self.pool = loop.run_until_complete(pgpool.instance().pool)

    def emit(self, record: LogRecord) -> None:
        """
        Synchronous emit wrapper; delegates to async_emit.

        Parameters
        ----------
        record : LogRecord
            The log record to emit.
        """
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
        import asyncpg  # Lazy import
        dt = datetime.fromtimestamp(record.created)
        level = record.levelname
        logger_name = record.name
        message = record.getMessage()
        obj = json.dumps(getattr(record, "obj", None)) if hasattr(record, "obj") else None

        query = """
            INSERT INTO logs ("tStamp", loglvl, logger, message, obj)
            VALUES ($1, $2, $3, $4, $5)
        """
        async with self.pool.acquire(timeout=10) as conn:  # type: asyncpg.Connection
            _ = await conn.execute(query, dt, level, logger_name, message, obj)

    def close(self) -> None:
        """
        No-op for close; shared pool is not closed here (use pgPool.close() at shutdown).
        """
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


async def setup_postgres_logging(settings: ResolvedSettingsDict) -> None:
    """
    Asynchronous setup for PostgreSQL logging infrastructure using pyPg tools.

    Ensures tablespace, database, extensions, and 'logs' table exist, creating them if missing.
    Initialises pgPool if not already done. Replicates incremental setup logic from pyPg's setup_db.py
    for consistency, but tailored to logging needs.

    Parameters
    ----------
    settings : ResolvedSettingsDict
        Resolved PostgreSQL connection settings.

    Returns
    -------
    None

    Raises
    ------
    asyncpg.PostgresError
        On connection or execution failures; caller should handle retries.
    """
    import asyncpg
    from infopypg import(                                                                 # Lazy import
        pgpool, 
        ResolvedSettingsDict,
    )

    # Ensure tablespace path exists
    if settings["TABLESPACE_PATH"]:
        Path(settings["TABLESPACE_PATH"]).mkdir(parents=True, exist_ok=True)

    # Connect to 'postgres' DB for TS/DB creation (like in setup_db.py)
    dsn = f"postgres://{settings['DB_USER']}:{settings['PASSWORD']}@{settings['DB_HOST']}:{settings['DB_PORT']}/postgres"
    conn = await asyncpg.connect(dsn)
    try:
        # Tablespace
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_tablespace WHERE spcname = $1)",
            settings["TABLESPACE_NAME"],
        )
        if not exists:
            _ = await conn.execute(
                f"CREATE TABLESPACE {settings['TABLESPACE_NAME']} LOCATION '{settings['TABLESPACE_PATH']}'"
            )

        # Database
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = $1)",
            settings["DB_NAME"],
        )
        if not exists:
            _ = await conn.execute(
                f"CREATE DATABASE {settings['DB_NAME']} OWNER {settings['DB_USER']} TABLESPACE {settings['TABLESPACE_NAME']}"
            )
    finally:
        await conn.close()

    # Initialise pgpool if not already (using converted settings)
    try:
        _ = pgpool.instance()
    except RuntimeError:
    
        pgpool.init(connection_dict=settings)

    # Now use pool for extensions and table
    pool = await pgpool.instance().pool
    async with pool.acquire() as conn:
        # Install missing extensions (like in setup_db.py)
        extentions_settings = settings.get("EXTENSIONS", [])
        if extentions_settings:
            for ext in extentions_settings:
                exists = await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = $1)",
                    ext,
                )
                if not exists:
                    _ = await conn.execute(f"CREATE EXTENSION {ext}")

        # Create 'logs' table if missing
        exists = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_name = 'logs'
            )
            """
        )
        if not exists:
            _ = await conn.execute(
                """
                CREATE TABLE public.logs (
                    idx BIGSERIAL PRIMARY KEY,
                    "tStamp" TIMESTAMP WITH TIME ZONE NOT NULL,
                    loglvl TEXT NOT NULL,
                    logger TEXT NOT NULL,
                    message TEXT NOT NULL,
                    obj JSONB
                );
                """
            )


def setup_logger(
    logger_name: str | None = None,
    log_location: str | ResolvedSettingsDict | None = None,
    log_file_maximum_size: int | None = None,
    backup_count: int | None = None,
    log_level: int | None = None,
) -> Logger:
    """
    Set up logging to a rotating file or PostgreSQL table with custom handling.

    If log_location is a ResolvedSettingsDict, uses PostgreSQL logging via infopypg's
    shared pgpool (initialises pgpool if not already done; creates infrastructure if
    missing). Otherwise, falls back to file-based with rotation.
    If parameters are not provided, uses environment variables or defaults.
    Avoids duplicate handlers by checking existing ones.

    Notes
    -----
    - For file: Log directory created relative to this module if not specified.
    - For Postgres: Creates 'logs' table if missing; inserts separate fields without
      rotation params. Initialises pgPool with provided settings if needed.
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
        If pgPool initialisation or access fails; check DB settings and availability.

    Examples
    --------
    >>> from logger import setup_logger
    >>> from pyPg.postgres_types import ResolvedSettingsDict
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
            # Postgres mode using pyPg
            loop = asyncio.get_event_loop()
            loop.run_until_complete(setup_postgres_logging(log_location))
            handler = PostgresHandler()
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

#  LocalWords:  tablespace fallbacks extname infopypg's pgpool infopypg psql dotenv
