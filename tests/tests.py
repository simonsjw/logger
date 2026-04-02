#!/usr/bin/env python3
"""
Pytest test suite for the logger package.

This module provides comprehensive tests for the logger package, covering
file-based logging (rotation, gzip compression, extra objects) and
PostgreSQL logging (lazy setup, inserts, and queries).

File-based tests run unconditionally. All PostgreSQL tests are automatically
skipped unless the POSTGRES_DB_TEST environment variable is set to a valid
JSON connection string.

Examples of pytest usage
------------------------
# Run all tests quietly
pytest tests/tests.py -q

# Run with verbose output
pytest tests/tests.py -v

# Run only file-based tests (fast, no DB required)
pytest tests/tests.py -v -k "file"

# Run full PostgreSQL integration tests
POSTGRES_DB_TEST='{ "db_user":"postgres", ... }' pytest tests/tests.py -v

# Run the critical lazy setup test in isolation (useful for diagnosing hangs)
POSTGRES_DB_TEST='{...}' pytest tests/tests.py::test_postgres_handler_lazy_setup -v --tb=short

# Run with maximum detail
POSTGRES_DB_TEST='{...}' pytest tests/tests.py -v --tb=long -rA
"""

import asyncio
import gzip
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import pytest
from infopypg import validate_dict_to_SettingsDict
from infopypg.psqlhelpers import async_resolve_SettingsDict_to_ResolvedSettingsDict

from logger import INFO, query_logs, setup_logger
from logger.core import PostgreSQLHandler


@pytest.fixture
def temp_log_path(tmp_path: Path) -> str:
    """Provide a temporary log file path for file-handler tests.

    Parameters
    ----------
    tmp_path : Path
        Built-in pytest temporary directory fixture.

    Returns
    -------
    str
        Full path to a test log file.
    """
    return str(tmp_path / "test_app.log")


def test_setup_logger_file_mode(temp_log_path: str) -> None:
    """Verify setup_logger returns a Logger with a file handler when given a path."""
    logger = setup_logger(
        logger_name="test_file",
        log_location=temp_log_path,
        log_level=INFO,
    )

    assert isinstance(logger, logging.Logger)
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], logging.handlers.RotatingFileHandler)           # type: ignore[attr-defined]


def test_file_logging_writes_correctly(temp_log_path: str) -> None:
    """Test that messages are written to the file in the expected format."""
    logger = setup_logger(log_location=temp_log_path, log_level=INFO)
    test_msg = "This is a test log message for file handler"

    logger.info(test_msg)

    assert Path(temp_log_path).exists()

    with open(temp_log_path, "r", encoding="utf-8") as f:                                 # Read back written log
        content = f.read()
    assert test_msg in content
    assert "INFO" in content


def test_file_logging_with_extra_obj(temp_log_path: str) -> None:
    """Verify extra={"obj": dict} is accepted and does not affect file output."""
    logger = setup_logger(log_location=temp_log_path, log_level=INFO)
    data = {"user_id": 42, "action": "login", "success": True}

    logger.info("User action logged", extra={"obj": data})

    with open(temp_log_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "User action logged" in content


def test_file_rotation_and_gzip(temp_log_path: str) -> None:
    """Force rotation with a tiny max size and verify gzip compression occurs."""
    logger = setup_logger(
        log_location=temp_log_path,
        log_file_maximum_size=200,                                                        # Small size forces quick rollover
        backup_count=2,
        log_level=INFO,
    )

    for i in range(30):                                                                   # Enough data to trigger rotation
        logger.info(f"Rotation test line {i} " * 20)

    gz_files = list(Path(temp_log_path).parent.glob("*.log.*.gz"))
    assert len(gz_files) > 0, "Expected at least one gzipped backup file"


@pytest.mark.asyncio(loop_scope="session")
async def test_postgres_handler_lazy_setup() -> None:
    """Test the critical lazy-setup path used by LLMClient / demo_reasoning.py.

    This test isolates _ensure_setup(), DatabaseBuilder.build(), and pool
    acquisition using the POSTGRES_DB_TEST environment variable. It fails
    fast with a clear assertion if the setup exceeds 30 seconds.
    """
    db_settings_str = os.getenv("POSTGRES_DB_TEST")
    if not db_settings_str:
        pytest.skip("Set POSTGRES_DB_TEST to run PostgreSQL lazy setup test")

    settings: dict[str, Any] = json.loads(db_settings_str)

    start_time = time.perf_counter()

    logger = setup_logger(log_location=settings, log_level=INFO)
    logger.info(
        "Testing lazy PostgreSQL handler setup",
        extra={"obj": {"test": True}},
    )

    await asyncio.sleep(0.8)                                                              # Allow background async work

    duration = time.perf_counter() - start_time
    print(f"\nPostgreSQL handler lazy setup completed in {duration:.2f} seconds")
    assert duration < 30.0, f"Setup took too long ({duration:.2f}s) – possible hang"

    # Clean up handler tasks before the test ends
    for handler in logger.handlers:
        if isinstance(handler, PostgreSQLHandler):
            await handler.aclose()
            break


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(
    not os.getenv("POSTGRES_DB_TEST"),
    reason="Set POSTGRES_DB_TEST to run PostgreSQL tests",
)
async def test_postgres_handler_setup_and_logging() -> None:
    """Integration test for PostgreSQL handler (lazy setup and insert)."""
    db_settings_str = os.getenv("POSTGRES_DB_TEST")
    settings: dict[str, Any] = json.loads(db_settings_str)

    logger = setup_logger(log_location=settings, log_level=INFO)
    test_msg = "PostgreSQL integration test message"
    test_obj = {"test_key": "test_value", "number": 123}

    logger.info(test_msg, extra={"obj": test_obj})

    await asyncio.sleep(0.5)                                                              # Allow async insert to complete

    # Clean up handler tasks before the test ends
    for handler in logger.handlers:
        if isinstance(handler, PostgreSQLHandler):
            await handler.aclose()
            break


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(
    not os.getenv("POSTGRES_DB_TEST"),
    reason="Set POSTGRES_DB_TEST to run PostgreSQL tests",
)
async def test_query_logs_function() -> None:
    """Integration test for query_logs on the logs table."""
    db_settings_str = os.getenv("POSTGRES_DB_TEST")
    raw_settings: dict[str, Any] = json.loads(db_settings_str)

    # Ensure table and infrastructure exist (idempotent)
    logger = setup_logger(log_location=raw_settings, log_level=INFO)
    logger.info(
        "Triggering lazy infrastructure setup for query test"
    )                                                                                     # Forces DatabaseBuilder

    # Resolve to ResolvedSettingsDict (uppercase keys required by PgPoolManager)
    validated = validate_dict_to_SettingsDict(raw_settings)
    resolved_settings = await async_resolve_SettingsDict_to_ResolvedSettingsDict(
        validated
    )

    results = await query_logs(
        "SELECT COUNT(*) AS log_count FROM logs LIMIT 1",
        resolved_settings,
    )

    assert results is not None
    assert isinstance(results, list)
    assert len(results) >= 0

    # Clean up handler tasks before the test ends
    for handler in logger.handlers:
        if isinstance(handler, PostgreSQLHandler):
            await handler.aclose()
            break
