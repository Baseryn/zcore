"""ZCore Core Configuration Module.

This module provides the core settings and configuration loading infrastructure for the
ZCore framework. It leverages Pydantic Settings (v2) for validation and environment
variable parsing, and registers itself within the dependency injection (DI) container
to support singleton management. A dynamic proxy is also provided to support lazy resolution
across the application lifecycle.
"""

import os
from typing import Any, TypeVar, cast

from pydantic import AliasChoices, BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from zcore.kernel.di import container

T = TypeVar("T", bound="Settings")


class DatabaseSettings(BaseModel):
    """Database connection and engine configuration schema."""

    url: str = "sqlite+aiosqlite:///zcore.db"
    pool_size: int = 5
    max_overflow: int = 10
    pool_recycle: int = 1800
    pool_pre_ping: bool = True
    echo: bool = False
    connect_args: dict[str, Any] = Field(default_factory=dict)
    execution_options: dict[str, Any] = Field(default_factory=dict)
    extra_engine_kwargs: dict[str, Any] = Field(default_factory=dict)


class LoggingSettings(BaseModel):
    """Structured logging configuration schema."""

    level: str = "INFO"
    json_format: bool | None = None
    log_sql_queries: bool = False
    slow_query_threshold_ms: float | None = None
    file_path: str | None = None
    max_bytes: int = 10 * 1024 * 1024
    backup_count: int = 5
    muted_loggers: list[str] = Field(
        default_factory=lambda: [
            "sqlalchemy.engine",
        ]
    )
    passthrough_loggers: list[str] = Field(
        default_factory=lambda: [
            "uvicorn",
            "uvicorn.access",
            "uvicorn.error",
        ]
    )
    intercept_loggers: list[str] = Field(
        default_factory=lambda: [
            "uvicorn",
            "uvicorn.access",
            "uvicorn.error",
        ]
    )
    custom_processors: list[Any] = Field(default_factory=list)


class Settings(BaseSettings):
    """Core settings and environment variables configuration for the ZCore framework.

    This class parses configuration variables from both environment variables and
    optional file-based sources. It manages configuration for database, logging,
    authentication, storage, timezones, pagination boundaries, caching, and stream capacities.
    """

    model_config = SettingsConfigDict(
        env_file=os.getenv("ENV_FILE", ".env"), extra="ignore", case_sensitive=True
    )

    DATABASE: DatabaseSettings = Field(default_factory=DatabaseSettings)
    DATABASE_URL: str = "sqlite+aiosqlite:///zcore.db"
    MAX_OVERFLOW: int = 10
    POOL_SIZE: int = 5
    DATABASE_TEST_URL: str = "sqlite+aiosqlite:///zcore_test.db"

    LOGGING: LoggingSettings = Field(default_factory=LoggingSettings)
    LOG_LEVEL: str = "INFO"
    LOG_SQL_QUERIES: bool | None = None

    TIMEZONE: str = "UTC"
    AUTO_CONVERT_TIMEZONE: bool = True

    SECRET_KEY: str = "zcore-insecure-fallback-secret-key-must-be-changed"
    PROJECT_NAME: str = "ZCore Application"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    AUTH_CACHE_TTL: int = 300

    PAGINATION_DEFAULT_SIZE: int = 20
    PAGINATION_MAX_SIZE: int = 100
    SEARCH_MAX_DEPTH: int = 3

    CACHE_LOCAL_MAXSIZE: int = 1000
    CACHE_DEFAULT_TTL: int = 3600
    CACHE_EVICTION_INTERVAL: int = 60

    STREAM_QUEUE_MAXSIZE: int = 100

    STORAGE_PATH: str = Field(
        default="./storage",
        validation_alias=AliasChoices("STORAGE_PATH", "STORAGE_BASE_PATH"),
    )
    STORAGE_URL_PREFIX: str = Field(
        default="/storage",
        validation_alias=AliasChoices("STORAGE_URL_PREFIX", "STORAGE_PREFIX"),
    )
    REDIS_URL: str | None = None
    DEBUG: bool = True

    @model_validator(mode="after")
    def _sync_settings(self) -> "Settings":
        if (
            self.DATABASE_URL != "sqlite+aiosqlite:///zcore.db"
            and self.DATABASE.url == "sqlite+aiosqlite:///zcore.db"
        ):
            self.DATABASE.url = self.DATABASE_URL
        elif (
            self.DATABASE.url != "sqlite+aiosqlite:///zcore.db"
            and self.DATABASE_URL == "sqlite+aiosqlite:///zcore.db"
        ):
            self.DATABASE_URL = self.DATABASE.url

        if self.POOL_SIZE != 5 and self.DATABASE.pool_size == 5:
            self.DATABASE.pool_size = self.POOL_SIZE
        elif self.DATABASE.pool_size != 5 and self.POOL_SIZE == 5:
            self.POOL_SIZE = self.DATABASE.pool_size

        if self.MAX_OVERFLOW != 10 and self.DATABASE.max_overflow == 10:
            self.DATABASE.max_overflow = self.MAX_OVERFLOW
        elif self.DATABASE.max_overflow != 10 and self.MAX_OVERFLOW == 10:
            self.MAX_OVERFLOW = self.DATABASE.max_overflow

        if self.LOG_LEVEL != "INFO" and self.LOGGING.level == "INFO":
            self.LOGGING.level = self.LOG_LEVEL
        elif self.LOGGING.level != "INFO" and self.LOG_LEVEL == "INFO":
            self.LOG_LEVEL = self.LOGGING.level

        if self.LOG_SQL_QUERIES is not None:
            self.LOGGING.log_sql_queries = self.LOG_SQL_QUERIES
        elif self.LOGGING.log_sql_queries is not False:
            self.LOG_SQL_QUERIES = self.LOGGING.log_sql_queries

        return self


def initialize_settings(settings_inst: Settings) -> None:
    """Register the settings instance in the IoC dependency injection container.

    Args:
        settings_inst: An instance of `Settings` to register into the global container.
    """
    container.register_singleton(settings_inst.__class__, settings_inst)
    if settings_inst.__class__ is not Settings:
        container.register_singleton(Settings, settings_inst)


def get_settings(settings_class: type[T] = Settings) -> T:  # type: ignore[assignment]
    """Retrieve the settings instance from the dependency injection container.

    Args:
        settings_class: The class type of the settings to resolve.

    Returns:
        The resolved settings instance of type `T`.
    """
    try:
        return cast(T, container.resolve(settings_class))
    except Exception:
        settings_inst = settings_class()
        initialize_settings(settings_inst)
        return cast(T, settings_inst)


class SettingsProxy:
    """Proxy object providing lazy attribute access to the active settings instance."""

    def __getattr__(self, name: str) -> Any:
        return getattr(get_settings(), name)


settings = SettingsProxy()
