"""ZCore Structured Logging Configuration.

This module initializes the application's logging pipeline. It integrates standard
Python `logging` with `structlog` via ProcessorFormatter to provide a multi-tier,
enterprise-ready logging subsystem supporting custom handlers, file rotation, and full dictConfig overrides.
"""

import logging
import logging.config
import sys
from typing import Any

import structlog

from zcore.config import LoggingSettings, settings


def setup_logging(
    config: LoggingSettings | dict[str, Any] | None = None,
    custom_processors: list[Any] | None = None,
    extra_handlers: list[logging.Handler] | None = None,
    dict_config: dict[str, Any] | None = None,
    log_level: int | str | None = None,
) -> None:
    """Configure the global structlog and standard logging engine.

    Supports zero-code declarative settings from `.env`, code-level handler and processor injections,
    and complete `logging.config.dictConfig` overrides.

    Args:
        config: An optional `LoggingSettings` instance or dictionary configuration.
        custom_processors: Optional list of additional structlog processors to include.
        extra_handlers: Optional list of Python `logging.Handler` instances to attach.
        dict_config: Optional full dictionary passed directly to `logging.config.dictConfig`.
        log_level: Optional explicit log level override.
    """
    if dict_config is not None:
        logging.config.dictConfig(dict_config)
        return

    if config is not None:
        if isinstance(config, dict):
            cfg = LoggingSettings.model_validate(config)
        elif isinstance(config, LoggingSettings):
            cfg = config
        else:
            cfg = getattr(settings, "LOGGING", LoggingSettings())
    else:
        cfg = getattr(settings, "LOGGING", LoggingSettings())

    if log_level is not None:
        if isinstance(log_level, str):
            level = getattr(logging, log_level.upper(), logging.INFO)
        else:
            level = log_level
    else:
        cfg_level_str = getattr(cfg, "level", "INFO")
        level = getattr(logging, cfg_level_str.upper(), logging.INFO)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if cfg.custom_processors:
        shared_processors.extend(cfg.custom_processors)
    if custom_processors:
        shared_processors.extend(custom_processors)

    is_json = (
        cfg.json_format
        if cfg.json_format is not None
        else not getattr(settings, "DEBUG", False)
    )

    if is_json:
        renderer = structlog.processors.JSONRenderer()
    else:
        try:
            from rich.traceback import install

            install(show_locals=False)
        except ImportError:
            pass
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    stdout_handler.setLevel(level)

    handlers: list[logging.Handler] = [stdout_handler]

    if cfg.file_path:
        from logging.handlers import RotatingFileHandler

        file_handler = RotatingFileHandler(
            cfg.file_path,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        handlers.append(file_handler)

    if extra_handlers:
        for h in extra_handlers:
            if not h.formatter:
                h.setFormatter(formatter)
            handlers.append(h)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    for h in handlers:
        root_logger.addHandler(h)
    root_logger.setLevel(level)

    for logger_name in cfg.muted_loggers:
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