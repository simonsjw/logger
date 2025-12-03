#!/usr/bin/env python3

"""
Type stubs for the logger module.
"""

from logging import Formatter, Handler, LogRecord, Logger
from pathlib import Path
from typing import override
from infopypg import ResolvedSettingsDict

class CustomFormatter(Formatter):
    @override
    def formatTime(self, record: LogRecord, datefmt: str | None = ...) -> str: ...

class PostgresHandler(Handler):
    def __init__(self) -> None: ...
    def emit(self, record: LogRecord) -> None: ...
    def close(self) -> None: ...

def namer(name: str) -> str: ...

def rotator(source: str | Path, dest: str | Path) -> None: ...

def setup_logger(
    logger_name: str | None = ...,
    log_location: str | ResolvedSettingsDict | None = ...,
    log_file_maximum_size: int | None = ...,
    backup_count: int | None = ...,
    log_level: int | None = ...,
) -> Logger: ...

#  LocalWords:  postgres
