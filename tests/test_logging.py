import importlib
import logging
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import structlog

from zcore.config import settings


@pytest.mark.parametrize(
    "is_atty, expected_renderer_cls",
    [
        (True, structlog.dev.ConsoleRenderer),
        (False, structlog.processors.JSONRenderer),
    ]
)
def test_logging_format_by_environment(
    monkeypatch: pytest.MonkeyPatch,
    is_atty: bool,
    expected_renderer_cls: type[Any]
) -> None:
    monkeypatch.setattr(sys.stderr, "isatty", lambda: is_atty)
    monkeypatch.setattr(settings, "DEBUG", is_atty)

    import zcore.logging.config as logging_config
    importlib.reload(logging_config)

    with patch("structlog.configure") as mock_configure:
        logging_config.setup_logging()
        mock_configure.assert_called_once()
        root_logger = logging.getLogger()
        assert len(root_logger.handlers) > 0
        formatter = root_logger.handlers[0].formatter
        assert isinstance(formatter.processors[-1], expected_renderer_cls)


def test_suppress_third_party_duplicate_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    loggers = ["uvicorn", "uvicorn.access", "sqlalchemy.engine"]
    dummy_handlers = {name: [logging.NullHandler()] for name in loggers}
    
    for name in loggers:
        logger = logging.getLogger(name)
        logger.handlers = list(dummy_handlers[name])
        logger.propagate = False
        
    import zcore.logging.config as logging_config
    importlib.reload(logging_config)
    
    with patch("structlog.configure"):
        logging_config.setup_logging()
        
    for name in loggers:
        logger = logging.getLogger(name)
        assert len(logger.handlers) == 0
        assert logger.propagate is True


@pytest.mark.parametrize("rich_installed", [True, False])
def test_rich_integration_debug_mode(monkeypatch: pytest.MonkeyPatch, rich_installed: bool) -> None:
    monkeypatch.setattr(settings, "DEBUG", True)
    
    import zcore.logging.config as logging_config
    importlib.reload(logging_config)
    
    mock_install = MagicMock()
    
    def mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "rich.traceback":
            if rich_installed:
                mock_module = MagicMock()
                mock_module.install = mock_install
                return mock_module
            raise ImportError()
        return original_import(name, *args, **kwargs)
        
    import builtins
    original_import = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", mock_import)
    
    with patch("structlog.configure"):
        logging_config.setup_logging()
        
    if rich_installed:
        mock_install.assert_called_once_with(show_locals=False)
    else:
        mock_install.assert_not_called()


def test_shared_processors_chain_integrity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DEBUG", False)
    
    import zcore.logging.config as logging_config
    importlib.reload(logging_config)
    
    with patch("structlog.configure") as mock_configure:
        logging_config.setup_logging()
        configured_processors = mock_configure.call_args[1].get("processors", [])
        
        assert structlog.contextvars.merge_contextvars in configured_processors
        assert structlog.stdlib.add_log_level in configured_processors
        assert structlog.processors.format_exc_info in configured_processors
        
        assert any(isinstance(p, structlog.stdlib.PositionalArgumentsFormatter) for p in configured_processors)
        assert any(isinstance(p, structlog.processors.TimeStamper) for p in configured_processors)


@pytest.mark.parametrize(
    "log_level_setting, expected_level",
    [
        ("DEBUG", logging.DEBUG),
        ("INFO", logging.INFO),
        ("WARNING", logging.WARNING),
    ]
)
def test_dynamic_log_level_configuration(
    monkeypatch: pytest.MonkeyPatch,
    log_level_setting: str,
    expected_level: int
) -> None:
    monkeypatch.setattr(settings, "LOG_LEVEL", log_level_setting)
    
    import zcore.logging.config as logging_config
    importlib.reload(logging_config)
    
    with patch("structlog.configure"):
        logging_config.setup_logging(log_level=log_level_setting)
        assert logging.getLogger().level == expected_level


def test_log_filtering_enforcement_in_action() -> None:
    class DummyHandler(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.records: list[logging.LogRecord] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.records.append(record)

    logger = logging.getLogger("test_filter_logger")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = DummyHandler()
    logger.handlers = [handler]

    logger.info("info_msg")
    assert len(handler.records) == 1
    assert handler.records[0].getMessage() == "info_msg"

    logger.debug("debug_msg")
    assert len(handler.records) == 1


def test_contextvars_binding_verification() -> None:
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="test_req_xyz")
    
    event_dict = structlog.contextvars.merge_contextvars(None, None, {"event": "hello"})
    assert event_dict.get("request_id") == "test_req_xyz"
    
    structlog.contextvars.clear_contextvars()


def test_factory_and_caching_configurations(monkeypatch: pytest.MonkeyPatch) -> None:
    import zcore.logging.config as logging_config
    importlib.reload(logging_config)
    
    with patch("structlog.configure") as mock_configure:
        logging_config.setup_logging()
        
        kwargs = mock_configure.call_args[1]
        assert isinstance(kwargs.get("logger_factory"), structlog.stdlib.LoggerFactory)
        assert kwargs.get("cache_logger_on_first_use") is True