import logging
import logging.handlers
from typing import Any

from infopypg.pgtypes import ResolvedSettingsDict

script_dir: str
log_path: str
log_dir: str

def setup_logger(
    logger_name: str | None = None,
    log_location: str | ResolvedSettingsDict = ...,
    log_file_maximum_size: int = ...,
    backup_count: int = 10,
    log_level: int = ...,
) -> logging.Logger: ...

class GzipRotatingFileHandler(logging.handlers.RotatingFileHandler):
    def doRollover(self) -> None: ...

class PostgreSQLHandler(logging.Handler):
    settings: ResolvedSettingsDict
    def __init__(self, settings: ResolvedSettingsDict) -> None: ...
    def emit(self, record: logging.LogRecord) -> None: ...

async def query_logs(
    query: str,
    params: list[Any] | None = None,
    settings: ResolvedSettingsDict | None = None,
) -> list[dict[str, Any]] | None: ...
