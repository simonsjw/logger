# Logger: Configurable Python Logging Module

[![Python Version](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/release/python-3120/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## Overview

This module provides a flexible logging setup for Python applications, supporting both rotating file handlers with gzip compression and integration with PostgreSQL databases via the custom `pyPg` library. It allows configuration through environment variables (loaded from a `.env` file using `python-dotenv`), with sensible defaults for file paths, rotation sizes, backup counts, and log levels.

Key features:
- **File-based logging**: Rotating logs with automatic gzip compression to save space; custom timestamps including microseconds.
- **PostgreSQL logging**: Asynchronous inserts into a `logs` table using `infopypg`'s shared connection pool; automatically creates the table (and supporting infrastructure like tablespace and database) if missing.
- **Environment-driven configuration**: Overrides via `.env` for paths, sizes, levels, etc.
- Designed for the `grok` conda environment; assumes dependencies like `python-dotenv`, `asyncpg`, and `infopypg` are available. 

Lazy loading is employed with `infopypg` so import order is:
- `logger`
- `infopypg` 

This logger prioritises efficiency and modularity, avoiding duplicate handlers and using asynchronous operations for database interactions to minimise blocking.

## Installation

This package is intended for use within the `grok` conda environment (as defined in `environment_grok.yml`). To install as an editable package:

1. Navigate to the project root (where `pyproject.toml` resides).
2. Activate the `grok` environment:
   ```
   conda activate grok
   ```
3. Install via pip:
   ```
   pip install -e .
   ```

This makes the `logger` module importable in your scripts.

### Dependencies

- Python 3.12+
- `python-dotenv` (for `.env` loading)
- `asyncpg` (for PostgreSQL interactions, via `pyPg`)
- Custom `pyPg` library (assumed available in the environment; provides `pgPool`, `postgres_types`, etc.)

Full environment details are in `environment_grok.yml`. Linting and type-checking are configured via `pyproject.toml` (using `ruff` and `basedpyright`).

## Usage

Import and set up the logger in your scripts using setup_logger with these optional arguments:
- logger_name: str | None = None
- log_location: str | ResolvedSettingsDict | None = None 
- log_file_maximum_size: int | None = None
- backup_count: int | None = None
- log_level: int | None = None


```python
from logger import setup_logger

# Basic file-based logger
logger = setup_logger()
logger.info("Application started.")
```

For PostgreSQL logging, pass a `ResolvedSettingsDict` (from `pyPg.postgres_types`):

```python
from pyPg.postgres_types import ResolvedSettingsDict
from logger import setup_logger

settings: ResolvedSettingsDict = {
    "DB_USER": "your_username",
    "DB_HOST": "localhost",
    "DB_PORT": "5432",
    "DB_NAME": "your_database",
    "PASSWORD": "your_password",
    "TABLESPACE_NAME": "your_tablespace",      # Optional
    "TABLESPACE_PATH": "/path/to/tablespace",  # Optional
    "EXTENSIONS": ["pgcrypto"],                # Optional
}

logger = setup_logger(log_location=settings, log_level=logging.DEBUG)
logger.info("Info message.", extra={"obj": {"key": "value"}})
```

### Configuration Options

- **Environment Variables** (via `.env` or system env; optional, with defaults):
  - `LOG_FILE_PATH`: Path to log file (default: `../logs/app.log` relative to module).
  - `LOG_MAX_SIZE`: Max size in bytes before rotation (default: 10MB).
  - `LOG_BACKUP_COUNT`: Number of backups to keep (default: 10).
  - `LOG_LEVEL`: Level as string (e.g., "DEBUG", "INFO"; default: "DEBUG").

- **Function Parameters** (in `setup_logger`):
  - `logger_name`: Optional name for the logger (defaults to root).
  - `log_location`: File path (str) or DB settings (`ResolvedSettingsDict`).
  - `log_file_maximum_size`: Max size (file mode only).
  - `backup_count`: Backup count (file mode only).
  - `log_level`: Logging level (int).

For PostgreSQL mode:
- The `logs` table is created if missing with columns: `idx` (BIGSERIAL PK), `tStamp` (TIMESTAMP WITH TIME ZONE), `loglvl` (TEXT), `logger` (TEXT), `message` (TEXT), `obj` (JSONB).
- Infrastructure (tablespace, database, extensions) is ensured incrementally using `pyPg` tools.

### Log Format

- **File mode**: "[timestamp; level; funcName] message; logger_name;" with microseconds.
- **PostgreSQL mode**: Direct field inserts; no formatting needed.

To include JSON objects: Use `extra={"obj": data}` in log calls (serialised to JSONB in DB).

## Examples

### File Logging with Rotation

```python
import logging
from logger import setup_logger

logger = setup_logger(log_level=logging.INFO)
logger.warning("This will rotate when file exceeds max size.")
```

### PostgreSQL Logging with Object

```python
logger.debug("Debug message.", extra={"obj": {"user_id": 123, "action": "login"}})
```

This inserts into the `logs` table with `obj` as JSONB.

## Development and Structure

- **Source Layout**:
  ```
  logger/
  ├── src/
  │   └── logger/
  │       ├── __init__.py   # Main module code and docstring
  │       └── __init__.pyi  # Type stubs (if applicable)
  ├── pyproject.toml        # Config for ruff, basedpyright, etc.
  └── README.md             # This file
  ```

- **Linting/Type-Checking**: Run `ruff check` and `basedpyright` from the root.
- **Testing**: Add tests in a future `tests/` directory; currently, rely on examples in docstrings.

## Contributing

Contributions are welcome! Please follow the code style in `__init__.py` (e.g., type hints, NumPy-style docstrings) and ensure compatibility with Python 3.12 and the `grok` environment.

## License

MIT License. See [LICENSE](LICENSE) for details.

---

For questions or issues, contact the maintainer. Last updated: December 01, 2025.
