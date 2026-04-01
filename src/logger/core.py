#!/usr/bin/env python3
"""
Flexible logging module with file and PostgreSQL support.

Supports rotating file logs with gzip and async PostgreSQL inserts via infopypg.
Lazy setup for PG: pool init and table creation on first log emission.
Provides query access to logs table.
"""

import asyncio
import gzip
import json
import logging
import logging.handlers
import os
import sys
import threading                                                                          # Thread-safe lock for cross-context lazy setup
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from asyncpg import Pool, PostgresConnectionError, Record
from infopypg.pgtypes import ResolvedSettingsDict

script_dir: str = os.path.dirname(os.path.abspath(__file__))
log_path: str = os.path.normpath(
    os.path.join(script_dir, "..", "..", "log", "default.log")
)
log_dir: str = os.path.dirname(log_path)                                                  # logger/log
os.makedirs(log_dir, exist_ok=True)                                                       # creates logger/log if needed


class LogEncoder(json.JSONEncoder):
    """
    Custom encoder for log objects, falling back to str for non-serialisable types.
    """

    def default(self, o: Any) -> str:
        return str(o)                                                                     # Fallback ensures arbitrary objects don't crash dumps.


def setup_logger(
    logger_name: str | None = None,
    log_location: str | dict[str, str | list[str]] | ResolvedSettingsDict = log_path,
    log_file_maximum_size: int = 10 * 1024 * 1024,                                        # 10MB
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
    log_location : str | dict[str, str | list[str]] | ResolvedSettingsDict
        File path, DB settings dict or resolved settings dict.
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
    else:                                                                                 # PG mode
        handler = PostgreSQLHandler(settings=log_location)

    logger.addHandler(handler)
    return logger


class GzipRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """
    Rotating file handler with gzip compression on rollover.
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

    Uses threading.Lock + double-check to guarantee one-time initialisation,
    eliminating repeated import/registration of Logs and the SAWarning.
    """

    _executor: ThreadPoolExecutor | None = None                                           # Class-level executor

    def __init__(
        self, settings: dict[str, str | list[str]] | ResolvedSettingsDict
    ) -> None:
        super().__init__()

        self.settings = settings
        self.resolved_settings: ResolvedSettingsDict | None = None
        self._initialized: bool = False
        self._setup_lock: threading.Lock = (
            threading.Lock()
        )                                                                                 # Thread-safe across event loops

    async def _ensure_setup(self) -> None:
        """
        Lazy one-time setup: resolve settings and run DatabaseBuilder exactly once.
        """
        if self._initialized:
            return

        if self._setup_lock.locked():                                                     # Double-check pattern
            return

        with self._setup_lock:                                                            # Thread-safe one-time setup
            if self._initialized:
                return

            from infopypg import DatabaseBuilder, SettingsDict
            from infopypg.psqlhelpers import (
                async_resolve_SettingsDict_to_ResolvedSettingsDict,
                validate_dict_to_SettingsDict,
            )

            if isinstance(self.settings, dict) and "host" in str(self.settings).lower():
                settings_dict: SettingsDict = validate_dict_to_SettingsDict(
                    self.settings
                )
                self.resolved_settings = (
                    await async_resolve_SettingsDict_to_ResolvedSettingsDict(
                        settings_dict
                    )
                )
            else:
                self.resolved_settings = self.settings                                    # Already resolved

            if self.resolved_settings is None:
                raise ConnectionError("Failed to resolve database settings")

            # Lazy import of model – breaks circular import while guaranteeing
            # registration happens only once (inside the locked block).
            from .log_spec import Logs                                                    # noqa: F401  # model registration side-effect

            spec_path: str = str(Path(script_dir) / "log_spec.py")
            builder = DatabaseBuilder(
                spec_path=spec_path,
                settings_dictionary=self.settings,
            )
            await builder.build()                                                         # Incremental and idempotent

            self._initialized = True

    @classmethod
    def _get_executor(cls) -> ThreadPoolExecutor:
        """
        Get or create the class-level executor.

        Returns
        -------
        ThreadPoolExecutor
            Single-worker executor for sync emits.
        """
        if cls._executor is None:
            cls._executor = ThreadPoolExecutor(max_workers=1)
            assert cls._executor is not None                                              # Narrow type for return
        return cls._executor

    def emit(self, record: logging.LogRecord) -> None:
        """
        Emit log record to PG without ever nesting event loops.
        """
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._async_emit(record))
        except RuntimeError:                                                              # No running loop → sync context
            executor = self._get_executor()
            executor.submit(asyncio.run, self._async_emit(record))

    async def _async_emit(self, record: logging.LogRecord) -> None:
        """
        Async insert log record.
        """
        from infopypg import PgPoolManager, ensure_partition_exists

        await self._ensure_setup()

        obj: Any | None = record.__dict__.get("obj")
        obj_json: str | None = (
            json.dumps(obj, cls=LogEncoder) if obj is not None else None
        )

        tstamp = datetime.now(timezone.utc)

        query = """
            INSERT INTO logs (tstamp, loglvl, logger, message, obj)
            VALUES ($1, $2, $3, $4, $5)
        """
        params = [tstamp, record.levelname, record.name, record.msg, obj_json]

        if self.resolved_settings is None:
            raise ConnectionError("No resolved connection settings for the database.")

        pool = await PgPoolManager.get_pool(self.resolved_settings)

        async with pool.acquire() as conn:
            # Get server current date for partition
            server_date_row: Record | None = await conn.fetchrow(
                "SELECT current_date AS today;"
            )

            if server_date_row is None:
                raise ValueError("Server date fetch failed.")

            server_date = server_date_row["today"]
            await ensure_partition_exists(
                pool,
                "logs",
                target_date=server_date,
                partition_key="tstamp",
                range_interval="daily",
                look_ahead_days=1,
            )

            await conn.execute(query, *params)


async def query_logs(
    query: str,
    resolved_settings: ResolvedSettingsDict,
    params: list[Any] | None = None,
) -> list[dict[str, Any]] | None:
    """
    Query the logs table asynchronously.
    """
    from infopypg import PgPoolManager, execute_query

    try:
        pool = await PgPoolManager.get_pool(resolved_settings)
    except RuntimeError:
        err_string: str = "PG pool not initialised; check settings."
        print(err_string, file=sys.stderr)
        raise RuntimeError(err_string) from None

    if pool is not None:
        return await execute_query(pool, query, params=params, fetch=True)
    return None
