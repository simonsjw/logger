#!/usr/bin/env python3
# tests/conftest.py
"""
Pytest configuration and fixtures for the logger package.

Fixtures supply consistent Logger instances and a pre-validated
ResolvedSettingsDict using the POSTGRES_DB_TEST environment variable.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import pytest
from infopypg import ResolvedSettingsDict, validate_dict_to_ResolvedSettingsDict

from logger import Logger, setup_logger


@pytest.fixture(scope="session")
def minimal_db_settings_dict() -> dict[str, Any]:
    """Return a raw dictionary parsed from the POSTGRES_DB_TEST environment
    variable.

    The environment variable contains a JSON string with uppercase keys and
    an extensions list. This fixture parses it once per session for efficiency.
    """
    env_str = os.getenv("POSTGRES_DB_TEST")
    if not env_str:
        raise RuntimeError("POSTGRES_DB_TEST environment variable is not set.")

    # Parse JSON and ensure correct types
    data: dict[str, Any] = json.loads(env_str)

    return data


@pytest.fixture(scope="session")
def db_settings(minimal_db_settings_dict: dict[str, Any]) -> ResolvedSettingsDict:
    """Return a fully validated ResolvedSettingsDict for use in tests.

    Validation and normalisation occur once per test session.
    """
    return validate_dict_to_ResolvedSettingsDict(minimal_db_settings_dict)


@pytest.fixture
def logger_no_db() -> Logger:
    """Return a Logger instance with file logging only (no database)."""
    return setup_logger(name="test_no_db", log_level=logging.DEBUG)


@pytest.fixture
def logger_with_db(db_settings: ResolvedSettingsDict) -> Logger:
    """Return a Logger instance configured with PostgreSQL logging."""
    return setup_logger(
        name="test_with_db",
        log_level=logging.DEBUG,
        db_settings=db_settings,
    )
