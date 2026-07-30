import os
import sys
import tempfile
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

def test_settings_type_coercion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEBUG", "False")
    monkeypatch.setenv("POOL_SIZE", "15")
    container._singletons.clear()
    fresh_settings = get_settings()
    assert fresh_settings.DEBUG is False
    assert fresh_settings.POOL_SIZE == 15

def test_settings_case_sensitivity(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform == "win32":
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("secret_key=lowercased-key-should-be-ignored\n")
            temp_filename = f.name
        try:
            container._singletons.clear()
            fresh_settings = Settings(_env_file=temp_filename)
            assert fresh_settings.SECRET_KEY == "zcore-insecure-fallback-secret-key-must-be-changed"
        finally:
            os.unlink(temp_filename)
    else:
        monkeypatch.setenv("secret_key", "lowercased-key-should-be-ignored")
        container._singletons.clear()
        fresh_settings = get_settings()
        assert fresh_settings.SECRET_KEY == "zcore-insecure-fallback-secret-key-must-be-changed"

def test_settings_extra_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RANDOM_ZCORE_ENVIRONMENT_VARIABLE", "ignore-me")
    container._singletons.clear()
    fresh_settings = get_settings()
    assert not hasattr(fresh_settings, "RANDOM_ZCORE_ENVIRONMENT_VARIABLE")

def test_settings_custom_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write("SECRET_KEY=env-file-custom-secret-999\nPROJECT_NAME=EnvFileApp")
        temp_filename = f.name
    try:
        container._singletons.clear()
        fresh_settings = Settings(_env_file=temp_filename)
        initialize_settings(fresh_settings)
        resolved = get_settings()
        assert resolved.SECRET_KEY == "env-file-custom-secret-999"
        assert resolved.PROJECT_NAME == "EnvFileApp"
    finally:
        os.unlink(temp_filename)

def test_settings_subclass_di_registration() -> None:
    container._singletons.clear()
    class CustomProjectSettings(Settings):
        CUSTOM_FIELD: str = "custom_value"
    custom_inst = CustomProjectSettings()
    initialize_settings(custom_inst)
    resolved_parent = container.resolve(Settings)
    resolved_child = container.resolve(CustomProjectSettings)
    assert resolved_parent is custom_inst
    assert resolved_child is custom_inst
    assert getattr(resolved_parent, "CUSTOM_FIELD") == "custom_value"

def test_settings_get_settings_fallback() -> None:
    container._singletons.clear()
    assert Settings not in container._singletons
    inst = get_settings()
    assert Settings in container._singletons
    assert container.resolve(Settings) is inst

def test_settings_proxy_missing_attribute() -> None:
    container._singletons.clear()
    with pytest.raises(AttributeError):
        _ = settings.NON_EXISTING_DATABASE_PORT

def test_settings_proxy_lazy_evaluation() -> None:
    container._singletons.clear()
    assert settings.SECRET_KEY == "zcore-insecure-fallback-secret-key-must-be-changed"
    new_inst = Settings(SECRET_KEY="lazy-evaluated-new-secret")
    initialize_settings(new_inst)
    assert settings.SECRET_KEY == "lazy-evaluated-new-secret"