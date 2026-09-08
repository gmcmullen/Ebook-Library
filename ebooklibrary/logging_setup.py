"""Console/file logging with ANSI colour, shared by every module."""
import logging
import sys
from pathlib import Path


class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


class ColoredFormatter(logging.Formatter):
    """Colour console output by level, without mutating the shared record."""

    def format(self, record):
        if record.levelno >= logging.ERROR:
            color = Colors.RED
        elif record.levelno >= logging.WARNING:
            color = Colors.YELLOW
        elif record.levelno >= logging.INFO:
            color = Colors.GREEN
        else:
            color = Colors.CYAN

        # Copy the record so the file handler still receives uncoloured text.
        record = logging.makeLogRecord(record.__dict__)
        record.msg = f"{color}{record.msg}{Colors.ENDC}"
        return super().format(record)


def get_logger() -> logging.Logger:
    """Return the package logger (handlers attached by setup_logging)."""
    return logging.getLogger("ebooklibrary")


def setup_logging(verbose: bool = False, log_file: Path = Path("libraryscan.log")) -> logging.Logger:
    logger = get_logger()
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False

    if logger.handlers:  # already configured
        return logger

    date_fmt = '%Y-%m-%d %H:%M:%S'
    fmt = '%(asctime)s - %(levelname)s - %(message)s'

    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(logging.Formatter(fmt, datefmt=date_fmt))
    file_handler.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(ColoredFormatter(fmt, datefmt=date_fmt))
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger
