import json
import logging
import logging.handlers
from _typeshed import Incomplete
from asyncpg import Pool as Pool, PostgresConnectionError as PostgresConnectionError
from infopypg.pgtypes import ResolvedSettingsDict as ResolvedSettingsDict
from typing import Any

script_dir: str
log_path: str
log_dir: str

class LogEncoder(json.JSONEncoder):
    def default(self, o: Any) -> str: ...

def setup_logger(logger_name: str | None = None, log_location: str | dict[str, str | list[str]] | ResolvedSettingsDict = ..., log_file_maximum_size: int = ..., backup_count: int = 10, log_level: int = ...) -> logging.Logger: ...

class GzipRotatingFileHandler(logging.handlers.RotatingFileHandler):
    def doRollover(self) -> None: ...

class PostgreSQLHandler(logging.Handler):
    settings: Incomplete
    resolved_settings: ResolvedSettingsDict | None
    def __init__(self, settings: dict[str, str | list[str]] | ResolvedSettingsDict) -> None: ...
    def emit(self, record: logging.LogRecord) -> None: ...

async def query_logs(query: str, resolved_settings: ResolvedSettingsDict, params: list[Any] | None = None) -> list[dict[str, Any]] | None: ...
