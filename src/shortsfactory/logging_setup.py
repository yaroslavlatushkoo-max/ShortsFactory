from __future__ import annotations

import logging
import sys
from pathlib import Path

from .config import Config


LOGGER_NAME = "shortsfactory"
_configured = False


def setup_logging(config: Config, level: int = logging.INFO) -> logging.Logger:
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    if _configured:
        return logger
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    stream = logging.StreamHandler(sys.stdout)
    stream.setLevel(level)
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    config.logs_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(config.logs_dir / "shortsfactory.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.propagate = False
    _configured = True
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def project_logger(project_workspace: Path, project_id: str) -> logging.Logger:
    logger = logging.getLogger(f"{LOGGER_NAME}.{project_id}")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        log_dir = project_workspace / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_dir / "pipeline.log", encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        )
        logger.addHandler(handler)
        logger.propagate = True
    return logger
