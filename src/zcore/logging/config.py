"""ZCore Structured Logging Configuration.

This module initializes the application's logging pipeline. It integrates standard
Python `logging` with `structlog` via ProcessorFormatter to provide structured,
context-aware diagnostics. It dynamically formats output as developer-friendly,
colorized text on interactive terminals or serialized JSON records on production stream targets.
"""

import logging
import sys
from typing import Any

import structlog

from zcore.config import settings


def setup_logging(
    custom_processors: list[Any] | None = None,
    log_level: int | None = None,
) -> None:
    """Configure the global structlog and standard logging engine.

    Configures a unified ProcessorFormatter handler on the root logger, routes all framework,
    application, and third-party logs through a shared pipeline, and formats outputs as
    colorized console streams in development or JSON streams in production.

    Args:
        custom_processors: Optional list of additional structlog processors to include in the pipeline.
        log_level: Optional logging level override.
    """
    level = log_level or (
        logging.DEBUG if getattr(settings, "DEBUG", False) else logging.INFO
    )

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if custom_processors:
        shared_processors.extend(custom_processors)

    if getattr(settings, "DEBUG", False):
        try:
            from rich.traceback import install

            install(show_locals=False)
        except ImportError:
            pass
        renderer = structlog.dev.ConsoleRenderer(colors=True)
    else:
        renderer = structlog.processors.JSONRenderer()

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.setLevel(level)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    for logger_name in (
        "uvicorn",
        "uvicorn.access",
        "uvicorn.error",
        "sqlalchemy.engine",
    ):
        log = logging.getLogger(logger_name)
        log.handlers.clear()
        log.propagate = True

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )