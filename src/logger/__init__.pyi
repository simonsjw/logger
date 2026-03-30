from .core import query_logs as query_logs, setup_logger as setup_logger
from logging import CRITICAL as CRITICAL, DEBUG as DEBUG, ERROR as ERROR, INFO as INFO, Logger as Logger, WARNING as WARNING

__all__ = ['setup_logger', 'query_logs', 'DEBUG', 'INFO', 'WARNING', 'CRITICAL', 'ERROR', 'Logger']
