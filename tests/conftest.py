# tests/conftest.py
import json
import logging
import os
from typing import Any

import pytest
import pytest_asyncio
from infopypg import validate_dict_to_SettingsDict
from infopypg.psqlhelpers import async_resolve_SettingsDict_to_ResolvedSettingsDict

from logger import setup_logger
from logger.core import PostgreSQLHandler


@pytest_asyncio.fixture(scope="session")
async def postgres_logger() -> logging.Logger:
    """Session-scoped fixture that guarantees the 'logs' table exists
    and returns a fully initialised logger (PostgreSQLHandler already set up).
    """
    db_settings_str = os.getenv("POSTGRES_DB_TEST")
    if not db_settings_str:
        pytest.skip(
            "POSTGRES_DB_TEST environment variable not set – skipping PostgreSQL tests"
        )
        # pytest.skip will prevent the fixture from being used; the return is never reached

    raw_settings: dict[str, Any] = json.loads(db_settings_str)

    # Validate settings (matches exactly what the handler expects)
    validated = validate_dict_to_SettingsDict(raw_settings)
    await async_resolve_SettingsDict_to_ResolvedSettingsDict(validated)

    # Register the logger exactly once
    logger = setup_logger(log_location=raw_settings, log_level="INFO")

    # Force table creation + pool setup (this is the library's official path)
    for handler in logger.handlers:
        if isinstance(handler, PostgreSQLHandler):
            await handler._ensure_setup()
            break
    else:
        pytest.fail("PostgreSQLHandler not found in logger.handlers")

    return logger
