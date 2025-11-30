#!/usr/bin/env python3

"""
Type stubs for the logger module.
"""

from logging import Formatter, Handler, LogRecord, Logger
from pathlib import Path
from typing import TypedDict, override

class ResolvedSettingsDict(TypedDict):
    DB_USER: str
    DB_HOST: str
    DB_PORT: str
    DB_NAME: str
    PASSWORD: str | None
    TABLESPACE_NAME: str
    TABLESPACE_PATH: str | None
    EXTENSIONS: list[str] | None

class CustomFormatter(Formatter):
    @override
    def formatTime(self, record: LogRecord, datefmt: str | None = ...) -> str: ...

class PostgresHandler(Handler):
    def __init__(self, settings: ResolvedSettingsDict) -> None: ...
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
