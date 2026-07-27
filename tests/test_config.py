import os
from typing import Any
import pytest

from zcore.config import Settings, get_settings, initialize_settings, settings
from zcore.kernel.di import container

@pytest.mark.parametrize(
    "env_key, env_val, expected_val, check_attr",
    [
        ("SECRET_KEY", "custom-env-secret-key-12345", "custom-env-secret-key-12345", "SECRET_KEY"),
        ("PROJECT_NAME", "ZCore Dynamic Config Test", "ZCore Dynamic Config Test", "PROJECT_NAME"),
    ]
)
def test_settings_environmental_loading(
    monkeypatch: pytest.MonkeyPatch,
    env_key: str,
    env_val: str,
    expected_val: str,
    check_attr: str
) -> None:
    monkeypatch.setenv(env_key, env_val)
    
    container._singletons.clear()
    
    fresh_settings = get_settings()
    assert getattr(fresh_settings, check_attr) == expected_val
    assert fresh_settings.DATABASE_URL == "sqlite+aiosqlite:///zcore.db"

@pytest.mark.parametrize(
    "secret_a, secret_b",
    [
        ("secret-instance-alpha", "secret-instance-beta"),
    ]
)
def test_settings_proxy_resolution(secret_a: str, secret_b: str) -> None:
    container._singletons.clear()

    settings_a = Settings(SECRET_KEY=secret_a)
    initialize_settings(settings_a)
    assert settings.SECRET_KEY == secret_a

    settings_b = Settings(SECRET_KEY=secret_b)
    initialize_settings(settings_b)
    assert settings.SECRET_KEY == secret_b