# Logger: Configurable Python Logging Module

[![Python Version](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/release/python-3120/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## Overview

This module provides a flexible logging setup for Python applications, supporting both rotating file handlers with gzip compression and integration with PostgreSQL databases via the custom `infopypg` library. It uses sensible defaults for file paths, rotation sizes, backup counts, and log levels, with overrides via function parameters.

Key features:
- **File-based logging**: Rotating logs with automatic gzip compression to save space; custom timestamps including microseconds.
- **PostgreSQL logging**: Asynchronous inserts into a `logs` table using `infopypg`'s shared connection pool; lazily initialises the pool and automatically creates the table (and supporting infrastructure) if missing via `DatabaseBuilder`.
- Designed for the `grok` conda environment; assumes dependencies like `asyncpg` and `infopypg` are available. 

Lazy loading is employed to avoid import cycles between `logger` and `infopypg`, with PostgreSQL setup deferred until the first log emission. This logger prioritises efficiency and modularity, avoiding duplicate handlers and using asynchronous operations for database interactions to minimise blocking.

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
- `asyncpg` (for PostgreSQL interactions, via `infopypg`)
- Custom `infopypg` library (assumed available in the environment; provides `PgPoolManager`, `pgtypes`, etc.)

Full environment details are in `environment_grok.yml`. Linting and type-checking are configured via `pyproject.toml` (using `ruff` and `basedpyright`).

## Usage

Import and set up the logger in your scripts using `setup_logger` with these optional arguments:
- `logger_name`: str | None = None
- `log_location`: str | ResolvedSettingsDict = "../logs/app.log" 
- `log_file_maximum_size`: int = 10 * 1024 * 1024  # 10MB
- `backup_count`: int = 10
- `log_level`: int = logging.DEBUG

```python
from logger import setup_logger

# Basic file-based logger
logger = setup_logger()
logger.info("Application started.")
```

For PostgreSQL logging, pass a connection dictionary `dict[str, str | list[str]]` (from `infopypg`):

```python
import logging
from logger import setup_logger

settings: dict = {
  "db_user": "postgres",
  "db_host": "127.0.0.1",
  "db_port": "5432",
  "db_name": "responsesdb",
  "password": "foobar123",
  "tablespace_name": "responses_db",
  "tablespace_path": "/mnt/HDD03_HIT_03TB/no_backup/pg03/responses_db",
  "extensions": ["uuid-ossp", "pg_trgm"]
}

logger = setup_logger(log_location=settings, log_level=logging.DEBUG)
logger.info("Info message.", extra={"obj": {"key": "value"}})
```

PostgreSQL can also be used by passing a resolved settings dictionary (from `infopypg`):

```python
import logging
from infopypg import validate_dict_to_ResolvedSettingsDict
from logger import setup_logger

settings: dict = {
  "db_user": "postgres",
  "db_host": "127.0.0.1",
  "db_port": "5432",
  "db_name": "responsesdb",
  "password": "foobar123",
  "tablespace_name": "responses_db",
  "tablespace_path": "/mnt/HDD03_HIT_03TB/no_backup/pg03/responses_db",
  "extensions": ["uuid-ossp", "pg_trgm"]
}

settings = validate_dict_to_ResolvedSettingsDict(settings)

logger = setup_logger(log_location=settings, log_level=logging.DEBUG)
logger.info("Info message.", extra={"obj": {"key": "value"}})
```

### Configuration Options

- **Function Parameters** (in `setup_logger`):
  - `logger_name`: Optional name for the logger (defaults to root).
  - `log_location`: File path (str), DB settings (`dict[str, str | list[str]]`) or resolved settings dictionary (`ResolvedSettingsDict`).
  - `log_file_maximum_size`: Max size (file mode only; default: 10MB).
  - `backup_count`: Backup count (file mode only; default: 10).
  - `log_level`: Logging level (int; default: DEBUG).

For PostgreSQL mode:
- The `logs` table is created if missing with columns: `idx` (BIGINT IDENTITY), `tstamp` (TIMESTAMP WITH TIME ZONE), `loglvl` (TEXT), `logger` (TEXT), `message` (TEXT), `obj` (JSONB).
- Infrastructure (tablespace, database, extensions) is ensured incrementally using `infopypg` tools.
- Setup is lazy: Pool initialisation and table creation occur on the first log emission.

### Log Format

- **File mode**: "[timestamp; level; funcName] message; logger_name;" with microseconds.
- **PostgreSQL mode**: Direct field inserts; no formatting needed.

To include JSON objects: Use `extra={"obj": data}` in log calls (serialised to JSONB in DB).

### Querying Logs

For PostgreSQL mode, query the `logs` table asynchronously using the module-level `query_logs` function:

```python
import asyncio
from logger import query_logs
from infopypg import validate_dict_to_ResolvedSettingsDict

settings: dict = {
  "db_user": "postgres",
  "db_host": "127.0.0.1",
  "db_port": "5432",
  "db_name": "responsesdb",
  "password": "foobar123",
  "tablespace_name": "responses_db",
  "tablespace_path": "/mnt/HDD03_HIT_03TB/no_backup/pg03/responses_db",
  "extensions": ["uuid-ossp", "pg_trgm"]
}

resolved_settings = validate_dict_to_ResolvedSettingsDict(settings)
        
async def main():
    results = await query_logs(
        "SELECT * FROM logs WHERE loglvl = $1 LIMIT 10", 
        resolved_settings,
        params=["INFO"]
    )
    print(results)  # list[dict[str, Any]]

asyncio.run(main())
```

## Examples

### File Logging with Rotation

```python
from logger import setup_logger, INFO

logger = setup_logger(log_level=INFO)
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
  │       ├── __init__.py   # exposed functionality for the module
  │       ├── __init__.pyi  # Type stubs 
  │       ├── core.py       # Implementation of the logger functionality.
  │       ├── core.pyi      # Type stubs
  │       ├── log_spec.py   # SQLAlchemy model spec for logs table (used in DatabaseBuilder)
  │       └── log_spec.pyi  # Type stubs
  ├── pyproject.toml        # Config for ruff, pyrefly, etc.
  └── README.md             # This file
  ```

- **Linting/Type-Checking**: Run `ruff check` and `pyrefly` from the root.
- **Testing**: Add tests in a future `tests/` directory; currently, rely on examples in docstrings.

## Full list of exposed functionality

All elements below can be imported from `logger`.

```python
from logger import (
    # Core logging library objects: 
    setup_logger,    # setup the logger
    query_logs,      # query logs set up on postgres. 
        
    # All following objects are unchanged from the base `logging` library.
    DEBUG,           # logging levels to use in setup_logger.
    INFO, 
    WARNING, 
    CRITICAL, 
    ERROR,

    Logger,          # type for the Logger returned by `setup_logger`
)
```

## Contributing

Contributions are welcome! Please follow the code style in `__init__.py` (e.g., type hints, NumPy-style docstrings) and ensure compatibility with Python 3.12 and the `grok` environment.

## License

MIT License. See [LICENSE](LICENSE) for details.

---

For questions or issues, contact the maintainer. Last updated: March 30, 2026.
