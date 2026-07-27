"""ZCore Structured Logging Configuration.

This module initializes the application's logging pipeline. It integrates standard 
Python `logging` with `structlog` to provide structured, context-aware diagnostics. 
It dynamically formats output as developer-friendly, colorized text on interactive terminals 
or serialized JSON records on production stream targets.
"""

import logging
import structlog

from zcore.config import settings


def setup_logging() -> None:
    """Configure the global structlog logging engine.

    Suppresses duplicate logging handlers from third-party libraries (like uvicorn or sqlalchemy)
    and routes all framework telemetry through a unified structlog pipeline.
    """
    for logger_name in ("uvicorn", "uvicorn.access", "sqlalchemy.engine"):
        log = logging.getLogger(logger_name)
        log.handlers = []
        log.propagate = True

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.format_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.PositionalArgumentsFormatter(),
    ]

    if getattr(settings, "DEBUG", False):
        try:
            from rich.traceback import install
            install(show_locals=False)
        except ImportError:
            pass
        renderer = structlog.dev.ConsoleRenderer(colors=True)
        log_level = logging.DEBUG
    else:
        renderer = structlog.processors.JSONRenderer()
        log_level = logging.INFO

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )