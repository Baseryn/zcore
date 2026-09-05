import os
import sys
import tempfile

import pytest

from zcore.config import (
    DatabaseSettings,
    LoggingSettings,
    Settings,
    get_settings,
    initialize_settings,
    settings,
)
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
    assert secret_a == settings.SECRET_KEY

    settings_b = Settings(SECRET_KEY=secret_b)
    initialize_settings(settings_b)
    assert secret_b == settings.SECRET_KEY


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
    assert resolved_parent.CUSTOM_FIELD == "custom_value"


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


def test_database_settings_model_defaults_and_custom() -> None:
    default_db = DatabaseSettings()
    assert default_db.url == "sqlite+aiosqlite:///zcore.db"
    assert default_db.pool_size == 5
    assert default_db.max_overflow == 10
    assert default_db.pool_recycle == 1800
    assert default_db.pool_pre_ping is True
    assert default_db.echo is False
    assert default_db.connect_args == {}
    assert default_db.execution_options == {}
    assert default_db.extra_engine_kwargs == {}

    custom_db = DatabaseSettings(
        url="postgresql+asyncpg://postgres:secret@localhost:5432/zcore_prod",
        pool_size=20,
        max_overflow=30,
        pool_recycle=3600,
        pool_pre_ping=False,
        echo=True,
        connect_args={"server_settings": {"jit": "off"}},
        execution_options={"isolation_level": "REPEATABLE READ"},
        extra_engine_kwargs={"isolation_level": "AUTOCOMMIT"},
    )
    assert custom_db.url == "postgresql+asyncpg://postgres:secret@localhost:5432/zcore_prod"
    assert custom_db.pool_size == 20
    assert custom_db.max_overflow == 30
    assert custom_db.pool_recycle == 3600
    assert custom_db.pool_pre_ping is False
    assert custom_db.echo is True
    assert custom_db.connect_args == {"server_settings": {"jit": "off"}}
    assert custom_db.execution_options == {"isolation_level": "REPEATABLE READ"}
    assert custom_db.extra_engine_kwargs == {"isolation_level": "AUTOCOMMIT"}


def test_logging_settings_model_defaults_and_custom() -> None:
    default_log = LoggingSettings()
    assert default_log.level == "INFO"
    assert default_log.json_format is None
    assert default_log.log_sql_queries is True
    assert default_log.slow_query_threshold_ms is None
    assert default_log.file_path is None
    assert default_log.muted_loggers == [
        "uvicorn",
        "uvicorn.access",
        "uvicorn.error",
        "sqlalchemy.engine",
    ]
    assert default_log.custom_processors == []

    dummy_processor = lambda logger, method, event_dict: event_dict
    custom_log = LoggingSettings(
        level="DEBUG",
        json_format=True,
        log_sql_queries=False,
        slow_query_threshold_ms=250.0,
        file_path="/var/log/zcore.log",
        muted_loggers=["custom.noisy.service"],
        custom_processors=[dummy_processor],
    )
    assert custom_log.level == "DEBUG"
    assert custom_log.json_format is True
    assert custom_log.log_sql_queries is False
    assert custom_log.slow_query_threshold_ms == 250.0
    assert custom_log.file_path == "/var/log/zcore.log"
    assert custom_log.muted_loggers == ["custom.noisy.service"]
    assert custom_log.custom_processors == [dummy_processor]


def test_settings_sync_validator_database_bidirectional() -> None:
    settings_from_flat = Settings(
        DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/syncdb",
        POOL_SIZE=25,
        MAX_OVERFLOW=50,
    )
    assert settings_from_flat.DATABASE.url == "postgresql+asyncpg://postgres:postgres@localhost:5432/syncdb"
    assert settings_from_flat.DATABASE.pool_size == 25
    assert settings_from_flat.DATABASE.max_overflow == 50

    settings_from_nested = Settings(
        DATABASE=DatabaseSettings(
            url="mysql+aiomysql://root:root@localhost:3306/syncdb",
            pool_size=12,
            max_overflow=24,
        )
    )
    assert settings_from_nested.DATABASE_URL == "mysql+aiomysql://root:root@localhost:3306/syncdb"
    assert settings_from_nested.POOL_SIZE == 12
    assert settings_from_nested.MAX_OVERFLOW == 24


def test_settings_sync_validator_logging_bidirectional() -> None:
    settings_from_flat = Settings(LOG_LEVEL="DEBUG")
    assert settings_from_flat.LOGGING.level == "DEBUG"

    settings_from_nested = Settings(
        LOGGING=LoggingSettings(level="ERROR")
    )
    assert settings_from_nested.LOG_LEVEL == "ERROR"


def test_timezone_settings_defaults() -> None:
    default_settings = Settings()
    assert default_settings.TIMEZONE == "UTC"
    assert default_settings.AUTO_CONVERT_TIMEZONE is True

    custom_settings = Settings(
        TIMEZONE="Asia/Tehran",
        AUTO_CONVERT_TIMEZONE=False,
    )
    assert custom_settings.TIMEZONE == "Asia/Tehran"
    assert custom_settings.AUTO_CONVERT_TIMEZONE is False