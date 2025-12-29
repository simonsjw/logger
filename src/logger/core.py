#!/usr/bin/env python3
"""
Flexible logging module with file and PostgreSQL support.

Supports rotating file logs with gzip and async PostgreSQL inserts via infopypg.
Lazy setup for PG: pool init and table creation on first log emission.
Provides query access to logs table.
"""

from __future__ import annotations

import asyncio
import gzip
import logging
import logging.handlers
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import debugpy

if TYPE_CHECKING:
    from infopypg.pgtypes import ResolvedSettingsDict


script_dir: str = os.path.dirname(os.path.abspath(__file__))
log_path: str = os.path.normpath(
    os.path.join(script_dir, "..", "..", "log", "default.log")
)
log_dir: str = os.path.dirname(log_path)  # logger/log
os.makedirs(log_dir, exist_ok=True)  # creates logger/log if needed


def setup_logger(
    logger_name: str | None = None,
    log_location: str | ResolvedSettingsDict = log_path,
    log_file_maximum_size: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 10,
    log_level: int = logging.DEBUG,
) -> logging.Logger:
    """
    Set up and return a configured logger.

    For file mode: rotating with gzip compression.
    For PG mode: custom async handler with lazy pool init and table ensure.

    Parameters
    ----------
    logger_name : str | None
        Name for the logger (defaults to root if None).
    log_location : str | ResolvedSettingsDict
        File path or DB settings dict.
    log_file_maximum_size : int
        Max file size before rotation (file mode only).
    backup_count : int
        Number of backups to keep (file mode only).
    log_level : int
        Logging level (e.g., logging.DEBUG).

    Returns
    -------
    logging.Logger
        Configured logger instance.

    Raises
    ------
    ValueError
        If invalid log_location type.
    """

    logger = logging.getLogger(logger_name)
    logger.setLevel(log_level)

    if isinstance(log_location, str):
        # File mode: rotating with gzip
        Path(log_location).parent.mkdir(parents=True, exist_ok=True)
        handler = GzipRotatingFileHandler(
            log_location,
            maxBytes=log_file_maximum_size,
            backupCount=backup_count,
        )
        formatter = logging.Formatter(
            "[%(asctime)s; %(levelname)s; %(funcName)s] %(message)s; %(name)s;",
            datefmt="%Y-%m-%d %H:%M:%S.%f",
        )
        handler.setFormatter(formatter)
    else:  # Assume ResolvedSettingsDict
        # PG mode: custom async handler
        handler = PostgreSQLHandler(settings=log_location)

    logger.addHandler(handler)
    return logger


class GzipRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """
    Rotating file handler with gzip compression on rollover.

    Parameters
    ----------
    filename : str
        Log file path.
    maxBytes : int
        Max size before rotation.
    backupCount : int
        Max backups.

    Returns
    -------
    None
        Logs to file with compression.
    """

    def doRollover(self) -> None:
        """
        Perform rollover: close, compress old, rename.
        """
        super().doRollover()
        if self.backupCount > 0:
            for i in range(self.backupCount - 1, 0, -1):
                s = f"{self.baseFilename}.{i}.gz"
                d = f"{self.baseFilename}.{i+1}.gz"
                if os.path.exists(s):
                    if os.path.exists(d):
                        os.remove(d)
                    os.rename(s, d)
            dfn = f"{self.baseFilename}.1"
            if os.path.exists(dfn):
                with open(dfn, "rb") as f_in, gzip.open(f"{dfn}.gz", "wb") as f_out:
                    f_out.writelines(f_in)
                os.remove(dfn)


class PostgreSQLHandler(logging.Handler):
    """
    Async PostgreSQL logging handler.

    Lazily inits pgpool and ensures logs table on first emit.
    Inserts logs async into logs table.

    Parameters
    ----------
    settings : ResolvedSettingsDict
        Resolved PG connection settings.

    Returns
    -------
    None
        Emits logs to PG.
    """

    def __init__(self, settings: ResolvedSettingsDict) -> None:
        super().__init__()
        self.settings = settings
        self._initialized = False

    async def _ensure_setup(self) -> None:
        """
        Lazy setup: init pool, ensure table/infra.
        """

        if self._initialized:
            return

        from infopypg.pgpool import pgpool
        from infopypg.setupdb import DatabaseBuilder

        pgpool.init(self.settings)  # Lazy; reuses if matching

        # Ensure infra and logs table via setupdb
        spec_path: str = script_dir + "/log_spec.py"
        builder = DatabaseBuilder(
            spec_path=spec_path,  # Assumes in same dir; adjust path if needed
            resolved_settings=self.settings,
        )
        await builder.build()  # Incremental: creates missing TS/DB/exts/tables

        self._initialized = True

    def emit(self, record: logging.LogRecord) -> None:
        """
        Emit log record to PG async.

        Parameters
        ----------
        record : logging.LogRecord
            Log record to insert.

        Raises
        ------
        Exception
            On insert failure (logged to stderr).
        """
        loop = asyncio.get_event_loop()
        loop.run_until_complete(self._async_emit(record))

    async def _async_emit(self, record: logging.LogRecord) -> None:
        """
        Async insert log record.
        """
        await self._ensure_setup()

        from infopypg.pgpool import pgpool

        obj = record.__dict__.get("obj")  # From extra={"obj": data}
        query = """
            INSERT INTO logs (loglvl, logger, message, obj)
            VALUES ($1, $2, $3, $4)
        """
        params = [record.levelname, record.name, record.msg, obj]

        pool = await pgpool.instance().pool
        async with pool.acquire() as conn:
            try:
                await conn.execute(query, *params)
            except Exception as e:
                # Fallback: print to stderr
                print(
                    f"PG log insert failed: {e}", file=sys.stderr
                )  # new in 3.12: file=sys.stderr for print


async def query_logs(
    query: str,
    params: list[Any] | None = None,
    settings: ResolvedSettingsDict | None = None,
) -> list[dict[str, Any]] | None:
    """
    Query the logs table asynchronously.

    Ensures PG setup if not initialised (requires settings if not via handler).
    Uses infopypg.loaddb.execute_query for execution.

    Parameters
    ----------
    query : str
        SQL query (e.g., "SELECT * FROM logs WHERE loglvl = $1").
    params : list[Any] | None
        Parameters for the query (optional).
    settings : ResolvedSettingsDict | None
        PG settings if not already initialised via logger.

    Returns
    -------
    list[dict[str, Any]] | None
        Query results as list of dicts (or None on failure).

    Raises
    ------
    RuntimeError
        If PG not initialised and no settings provided.
    """

    # Ensure setup (lazy init if needed)
    from infopypg.loaddb import execute_query
    from infopypg.pgpool import pgpool

    try:
        await pgpool.instance().pool  # Check if initialised
    except RuntimeError:
        if settings is None:
            err_string: str = "PG pool not initialised; provide settings."
            print(err_string, file=sys.stderr)
            raise RuntimeError(err_string)

        handler = PostgreSQLHandler(settings)  # Temp for setup
        await handler._ensure_setup()

    return await execute_query(query, params=params, fetch=True)


#  LocalWords:  ValueError
