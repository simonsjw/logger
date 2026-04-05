#!/usr/bin/env python3
# tests/tests.py
"""
Test suite for the logger package.

Covers file-based logging, asynchronous database logging, and query functionality.
"""

from __future__ import annotations

import logging

import pytest

from logger.core import Logger, query_logs, setup_logger


def test_setup_logger_creates_file_logger(logger_no_db: Logger) -> None:
    """Verify that setup_logger produces a functional file-only logger.

    The logger must write messages and create the expected log file.
    """
    logger_no_db.info("Test info message")
    logger_no_db.error("Test error message")

    assert logger_no_db.log_file.exists()
    content = logger_no_db.log_file.read_text(encoding="utf-8")
    assert "Test info message" in content
    assert "Test error message" in content


@pytest.mark.asyncio
async def test_async_logging_with_db(logger_with_db: Logger) -> None:
    """Verify asynchronous logging writes both to file and to the database."""
    await logger_with_db.ainfo("Async info message")
    await logger_with_db.aerror("Async error message")

    # Verify file output
    content = logger_with_db.log_file.read_text(encoding="utf-8")
    assert "Async info message" in content
    assert "Async error message" in content


@pytest.mark.asyncio
async def test_query_logs_returns_data(db_settings: ResolvedSettingsDict) -> None:
    """Verify that query_logs can retrieve inserted log records.

    This test inserts a record then queries it back using the correct
    column names from the logs table (loglvl, logger, message, obj).
    """
    # Insert a known record for this test
    logger = setup_logger(name="query_test", db_settings=db_settings)
    await logger.ainfo("Test message for query")

    query = """
        SELECT idx, tstamp, loglvl, logger, message, obj
        FROM logs
        WHERE logger = $1
        ORDER BY tstamp DESC
        LIMIT 5
    """
    rows = await query_logs(query, db_settings, params=["query_test"])

    assert len(rows) > 0, "No rows returned from logs table after insert"
    assert any(row["message"] == "Test message for query" for row in rows)


def test_logger_respects_log_level(logger_no_db: Logger) -> None:
    """Confirm that the logger honours the supplied log_level."""
    logger_no_db.logger.setLevel(logging.WARNING)

    logger_no_db.info("This should be filtered")
    logger_no_db.error("This should appear")                                              # ERROR is above WARNING

    content = logger_no_db.log_file.read_text(encoding="utf-8")
    assert "This should be filtered" not in content
    assert "This should appear" in content
