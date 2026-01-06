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
from pathlib import Path
from typing import TYPE_CHECKING, Any

import debugpy
from asyncpg import Pool, PostgresConnectionError, Record

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

    Lazily inits PgPoolManager  and ensures logs table on first emit.
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

        from infopypg import DatabaseBuilder, PgPoolManager

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

        Handles explicit tstamp to satisfy partitioning routing before defaults.
        Uses UTC for consistency with likely server config; adjust if server timezone differs.
        """
        await self._ensure_setup()
        from datetime import date, datetime, timezone

        from infopypg import PgPoolManager, ensure_partition_exists

        obj = record.__dict__.get("obj")  # From extra={"obj": data}
        tstamp = datetime.now(
            timezone.utc
        )  # Client-side now(); mimics func.now() but uses local clock.

        query = """
            INSERT INTO logs (tstamp, loglvl, logger, message, obj)
            VALUES ($1, $2, $3, $4, $5)
        """
        params = [tstamp, record.levelname, record.name, record.msg, obj]
        pool = await PgPoolManager.get_pool(self.settings)  # Targets resolved DB

        async with pool.acquire() as conn:
            # Get server current date for partition
            server_date_row = None
            try:
                server_date_row: Record | None = await conn.fetchrow(
                    "SELECT current_date AS today;"
                )
            except PostgresConnectionError as e:
                print(
                    f"database did not return the current date: {e}", file=sys.stderr
                )  # Inline: new in 3.12.

            if server_date_row is None:
                print(f"Current date not returned. server_date_row set to None.")
                raise ValueError

            try:
                server_date = server_date_row["today"]
                await ensure_partition_exists(
                    pool,
                    "logs",
                    target_date=server_date,
                    partition_key="tstamp",
                    range_interval="daily",
                    look_ahead_days=1,
                )
            except Exception as e:
                print(f"Log partition creation failed: {e}", file=sys.stderr)

            try:
                await conn.execute(query, *params)
            except Exception as e:
                print(f"PG log insert failed: {e}", file=sys.stderr)


async def query_logs(
    query: str,
    settings: ResolvedSettingsDict,
    params: list[Any] | None = None,
) -> list[dict[str, Any]] | None:
    """
    Query the logs table asynchronously.

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

    # Lazy init of infopypg.
    from infopypg import PgPoolManager, execute_query

    pool: Pool | None = None
    try:
        pool = await PgPoolManager.get_pool(settings)  # Targets resolved DB
    except RuntimeError:
        if settings is None:
            err_string: str = "PG pool not initialised; check settings."
            print(err_string, file=sys.stderr)
            raise RuntimeError(err_string)

    if not pool is None:
        return await execute_query(pool, query, params=params, fetch=True)


#  LocalWords:  ValueError tstamp asctime loglvl RuntimeError LogRecord async inits
#  LocalWords:  ResolvedSettingsDict PgPoolManager
