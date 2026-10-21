"""Configure the application log file."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(log_file: Path, verbose: bool = False) -> None:
    """Send INFO and above to the log file; with verbose, also echo them to stderr."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("busbidder")
    logger.setLevel(logging.INFO)
    for handler in list(logger.handlers):
        if not isinstance(handler, logging.NullHandler):
            logger.removeHandler(handler)
            handler.close()

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(file_handler)

    if verbose:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        logger.addHandler(console)
