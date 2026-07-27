import importlib
import sys
from typing import Any, Type
from unittest.mock import patch
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
    expected_renderer_cls: Type[Any]
) -> None:
    monkeypatch.setattr(sys.stderr, "isatty", lambda: is_atty)
    monkeypatch.setattr(settings, "DEBUG", is_atty)

    import zcore.logging.config as logging_config
    importlib.reload(logging_config)

    with patch("structlog.configure") as mock_configure:
        logging_config.setup_logging()
        mock_configure.assert_called_once()
        configured_processors = mock_configure.call_args[1].get("processors", [])
        assert len(configured_processors) > 0
        assert isinstance(configured_processors[-1], expected_renderer_cls)